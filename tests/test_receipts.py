import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from search_receipts.core import InputError, check_bytes, load_rules, normalize, timestamp

ROOT = Path(__file__).resolve().parents[1]
DATE = "2026-10-29T12:00:00Z"


def row(**changes):
    result = dict(query_id="q", result_id="a", url="https://example.org/a",
                  text="I need a tool", text_complete=True, likes=100, likes_observed_at=DATE)
    result.update(changes)
    return result


def rules(rule=None, queries=None):
    return json.dumps(dict(schema_version=1, queries=queries if queries is not None else {
        "q": rule if rule is not None else dict(min_likes=100, all_phrases=["need a tool"])
    })).encode()


def run_rows(rows, config=None, **limits):
    data = b"".join(json.dumps(value, ensure_ascii=False).encode() + b"\n" for value in rows)
    return check_bytes(data, config if config is not None else rules(), **limits)


class Decisions(unittest.TestCase):
    def test_six_row_end_to_end(self):
        report = check_bytes((ROOT / "examples/results.jsonl").read_bytes(), (ROOT / "examples/rules.json").read_bytes())
        self.assertEqual([r["classification"] for r in report["rows"]],
                         ["accepted", "accepted", "rejected", "rejected", "unverifiable", "unverifiable"])
        self.assertEqual(report["summary"]["total_records"], 6)
        self.assertEqual(report["exit_code"], 3)
        self.assertEqual(report["summary"]["unknown_criteria"], 2)

    def test_exact_threshold_and_zero(self):
        for minimum, value in [(100, 100), (0, 0), (0, 1)]:
            with self.subTest(minimum=minimum, value=value):
                self.assertEqual(run_rows([row(likes=value)], rules(dict(min_likes=minimum)))["exit_code"], 0)
        self.assertEqual(run_rows([row(likes=0)])["exit_code"], 1)

    def test_bad_likes_are_unknown_not_invalid_or_zero(self):
        for value in [None, -1, 2.5, 100.0, "100", True, [], {}]:
            with self.subTest(value=value):
                report = run_rows([row(likes=value)], rules(dict(min_likes=0)))
                self.assertEqual(report["rows"][0]["classification"], "unverifiable")
                self.assertEqual(report["summary"]["invalid"], 0)
                self.assertEqual(report["rows"][0]["criteria"][0]["observed"]["likes"], value)
        missing = row()
        del missing["likes"]
        self.assertEqual(run_rows([missing], rules(dict(min_likes=0)))["exit_code"], 2)

    def test_missing_or_invalid_observation_prevents_comparison(self):
        for value in [None, "2026-10-29T12:00:00", "not a date", 123, "2026-02-30T00:00:00Z"]:
            with self.subTest(value=value):
                report = run_rows([row(likes=1, likes_observed_at=value)])
                self.assertEqual(report["rows"][0]["criteria"][0]["status"], "unknown")
                self.assertEqual(report["rows"][0]["classification"], "unverifiable")

    def test_old_observation_does_not_claim_freshness(self):
        report = run_rows([row(likes_observed_at="2000-01-01T00:00:00Z")])
        self.assertEqual(report["exit_code"], 0)
        self.assertIn("valid only at supplied observation time", report["rows"][0]["criteria"][0]["reason"])

    def test_all_and_any_truth_tables(self):
        for name in ["all_phrases", "any_phrases"]:
            for complete in [True, False, None]:
                for text, present in [("red blue", 2), ("red", 1), ("green", 0), (None, 0)]:
                    with self.subTest(name=name, complete=complete, text=text):
                        report = run_rows([row(text=text, text_complete=complete)], rules({name: ["red", "blue"]}))
                        absent_status = "fail" if complete and text is not None else "unknown"
                        if name == "all_phrases":
                            expected = "pass" if present == 2 else absent_status
                        else:
                            expected = "pass" if present else absent_status
                        check = report["rows"][0]["criteria"][0]
                        self.assertEqual(check["status"], expected)
                        self.assertEqual(len(check["phrases"]), 2)
                        self.assertEqual(check["phrases"][0]["status"], "pass" if present else absent_status)

    def test_unknown_completeness_and_missing_text(self):
        source = row(text="no match")
        del source["text_complete"]
        self.assertEqual(run_rows([source])["exit_code"], 2)
        source = row(text=None, text_complete=True)
        self.assertEqual(run_rows([source])["exit_code"], 2)

    def test_unicode_casefold_whitespace_literal_substrings(self):
        text = "Straße\tCAFE\u0301\n\u00a0need\u2003a    tool"
        report = run_rows([row(text=text)], rules(dict(all_phrases=["STRASSE", "café", "Need a Tool", "a to"])))
        self.assertEqual(report["exit_code"], 0)
        for text in ["I needed a tool", "need an app", "NEED-A-TOOL"]:
            self.assertEqual(run_rows([row(text=text)])["exit_code"], 1)
        self.assertEqual(normalize("Ａ"), "ａ")
        self.assertNotEqual(normalize("Ａ"), normalize("A"))

    def test_rejection_keeps_all_unknown_reasons(self):
        report = run_rows([row(likes=99, text="excerpt", text_complete=False, published_at="bad")],
                          rules(dict(min_likes=100, all_phrases=["need a tool"], published_at={"from": DATE})))
        result = report["rows"][0]
        self.assertEqual(result["classification"], "rejected")
        self.assertEqual([c["status"] for c in result["criteria"]], ["fail", "unknown", "unknown"])
        self.assertEqual(report["summary"]["unknown_criteria"], 2)
        self.assertEqual(report["summary"]["rows_with_unknown"], 1)
        self.assertEqual(report["exit_code"], 3)

    def test_groups_use_only_their_own_rules(self):
        config = rules(queries={"q": dict(min_likes=100), "other": dict(any_phrases=["offline"], min_likes=0)})
        report = run_rows([row(), row(query_id="other", result_id="b", text="offline", likes=0)], config)
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(len(report["queries"]), 2)
        self.assertEqual(report["summary"]["accepted"], 2)
        report = run_rows([row(), row(query_id="missing", result_id="b")], config)
        self.assertEqual(report["rows"][1]["classification"], "unverifiable")
        self.assertEqual(report["rows"][1]["criteria"][0]["criterion"], "query_rules")
        self.assertEqual(report["exit_code"], 2)

    def test_second_fixture_group(self):
        report = check_bytes((ROOT / "examples/results.jsonl").read_bytes() + (ROOT / "examples/groups.jsonl").read_bytes(),
                             (ROOT / "examples/rules.json").read_bytes())
        self.assertEqual(report["summary"]["accepted"], 3)
        self.assertEqual(next(g for g in report["queries"] if g["query_id"] == "notes")["accepted"], 1)

    def test_duplicates_preserved_global_and_marked(self):
        values = [row(), row(likes=101), row(query_id="other"), row(result_id="different")]
        report = run_rows(values, rules(queries={"q": dict(min_likes=0), "other": dict(min_likes=0)}))
        self.assertEqual(report["summary"]["total_records"], 4)
        self.assertEqual(report["summary"]["accepted"], 4)
        self.assertEqual(report["summary"]["distinct_result_ids"], 2)
        self.assertEqual(report["duplicates"], [dict(result_id="a", lines=[1, 2, 3])])
        self.assertEqual([r["duplicate_id"] for r in report["rows"]], [True, True, True, False])
        self.assertEqual(report["rows"][0]["source"]["likes"], 100)
        self.assertEqual(report["rows"][1]["source"]["likes"], 101)
        self.assertEqual(report["exit_code"], 2)

    def test_description_is_never_parsed(self):
        report = run_rows([row(likes=1)], rules(dict(description="min_faves:100000000 OR magic", min_likes=0)))
        self.assertEqual(report["exit_code"], 0)

    def test_no_network_or_url_open(self):
        with patch("socket.socket", side_effect=AssertionError("Network accessed")), patch("webbrowser.open", side_effect=AssertionError("URL opened")):
            self.assertEqual(run_rows([row(url="file:///do-not-open")])["exit_code"], 0)


class Dates(unittest.TestCase):
    def test_offsets_inclusive_and_one_sided_windows(self):
        for value in [DATE, "2026-10-29T08:00:00-04:00", "2026-10-29T13:00:00+01:00", "2026-10-29t12:00:00z"]:
            with self.subTest(value=value):
                config = rules(dict(published_at={"from": DATE, "through": DATE}))
                self.assertEqual(run_rows([row(published_at=value)], config)["exit_code"], 0)
        for bounds in [{"from": DATE}, {"through": DATE}]:
            self.assertEqual(run_rows([row(published_at=DATE)], rules(dict(published_at=bounds)))["exit_code"], 0)
        for value in ["2026-10-29T11:59:59Z", "2026-10-29T12:00:01Z"]:
            self.assertEqual(run_rows([row(published_at=value)], config)["exit_code"], 1)

    def test_fractional_precision_never_truncates(self):
        config = rules(dict(published_at={"through": "2026-10-29T12:00:00.1234567Z"}))
        for value, code in [("2026-10-29T12:00:00.12345670Z", 0), ("2026-10-29T12:00:00.1234568Z", 1), (DATE, 0)]:
            self.assertEqual(run_rows([row(published_at=value)], config)["exit_code"], code)

    def test_unsupported_dates_preserve_original(self):
        for value in [None, "", "2026-10-29", "2026-10-29T12:00:00", "2026-02-30T00:00:00Z", "2026-10-29T12:00:00+24:00", "2026-10-29T12:00:00+00:99", "2026-10-29T12:00:00-00:00", "2016-12-31T23:59:60Z", 17]:
            with self.subTest(value=value):
                report = run_rows([row(published_at=value)], rules(dict(published_at={"from": DATE})))
                self.assertEqual(report["exit_code"], 2)
                self.assertEqual(report["rows"][0]["criteria"][0]["observed"], value)
                self.assertIsNone(timestamp(value))


class InputAndRules(unittest.TestCase):
    def test_empty_rule_configs_rejected(self):
        bad = [{}, {"description": "label"}, {"all_phrases": []}, {"any_phrases": []}, {"published_at": {}},
               {"min_likes": -1}, {"min_likes": True}, {"min_likes": 1.0}, {"all_phrases": [" "]},
               {"all_phrases": "tool"}, {"any_phrases": [1]}, {"typo": 1},
               {"published_at": {"from": "2026-10-29"}}, {"published_at": {"after": DATE}},
               {"published_at": {"from": DATE, "through": "2020-01-01T00:00:00Z"}}, {"description": 1, "min_likes": 1}]
        for rule in bad:
            with self.subTest(rule=rule), self.assertRaises(InputError):
                load_rules(rules(rule))
        for data in [b'{}', b'[]', b'{"schema_version":true,"queries":{}}', rules(queries={}),
                     b'{"schema_version":1,"schema_version":1,"queries":{}}', b'NaN', b'\xff']:
            with self.subTest(data=data), self.assertRaises(InputError):
                load_rules(data)
        self.assertEqual(load_rules(rules(dict(min_likes=0)))["queries"]["q"]["min_likes"], 0)
        with self.assertRaises(InputError):
            load_rules(rules(queries={"q": {"min_likes": 0}, "empty": {}}))

    def test_invalid_json_blank_utf8_and_structure_are_counted(self):
        data = b'{bad}\n\n\xff\n[]\n' + json.dumps(row(result_id="")).encode() + b'\n' + json.dumps(row()).encode()
        report = check_bytes(data, rules())
        self.assertEqual(report["summary"]["total_records"], 6)
        self.assertEqual(report["summary"]["invalid"], 5)
        self.assertEqual(report["summary"]["accepted"], 1)
        self.assertEqual(sum(g["total_records"] for g in report["queries"]), 6)
        self.assertEqual([r["line"] for r in report["rows"]], list(range(1, 7)))
        self.assertIn("Blank line", report["rows"][1]["errors"])
        self.assertIn("raw_base64", report["rows"][2])
        self.assertEqual(report["exit_code"], 4)

    def test_invalid_field_types_and_strict_json(self):
        for changes in [dict(query_id=None), dict(url=5), dict(text=[]), dict(text_complete="false"), dict(result_id=True)]:
            with self.subTest(changes=changes):
                self.assertEqual(run_rows([row(**changes)])["summary"]["invalid"], 1)
        for data in [b'{"query_id":"a","query_id":"b"}\n', b'NaN\n', b'{"x":1e999}\n', b'{"x":"\\ud800"}\n']:
            self.assertEqual(check_bytes(data, rules())["summary"]["invalid"], 1)

    def test_empty_input_and_line_endings(self):
        report = check_bytes(b"", rules())
        self.assertEqual(report["exit_code"], 4)
        self.assertEqual(report["summary"]["total_records"], 0)
        self.assertEqual(check_bytes(b"\n", rules())["summary"]["invalid"], 1)
        raw = json.dumps(row()).encode()
        self.assertEqual(check_bytes(raw, rules())["summary"]["total_records"], 1)
        self.assertEqual(check_bytes(raw + b"\r\n", rules())["exit_code"], 0)

    def test_line_and_record_limits(self):
        data = json.dumps(row()).encode() + b"\n"
        self.assertEqual(check_bytes(data, rules(), max_line_bytes=len(data)-1)["exit_code"], 0)
        report = check_bytes(data, rules(), max_line_bytes=len(data)-2)
        self.assertEqual(report["summary"]["invalid"], 1)
        self.assertIn("Line exceeds byte limit", report["rows"][0]["errors"])
        check_bytes(data, rules(), max_records=1)
        with self.assertRaises(InputError):
            check_bytes(data + data, rules(), max_records=1)

    def test_hashes_and_effective_config(self):
        data = json.dumps(row()).encode()
        config = rules()
        report = check_bytes(data, config)
        self.assertEqual(report["files"]["input"]["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(report["files"]["rules"]["sha256"], hashlib.sha256(config).hexdigest())
        self.assertEqual(report["effective_rules"], json.loads(config))
        self.assertEqual(report["schema_version"], 1)

    def test_all_exit_conditions_survive_together(self):
        data = json.dumps(row(likes=99, text="preview", text_complete=False)).encode() + b"\ninvalid\n"
        report = check_bytes(data, rules())
        self.assertEqual(report["exit_code"], 7)
        self.assertTrue(all(report["conditions"].values()))
        self.assertEqual(sum(report["summary"][s] for s in ["accepted", "rejected", "unverifiable", "invalid"]), 2)


class CLI(unittest.TestCase):
    def invoke(self, *args, cwd=None):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT / "src")
        return subprocess.run([sys.executable, "-m", "search_receipts", *map(str, args)], cwd=cwd,
                              capture_output=True, text=True, env=env)

    def test_demo_installed_resources_and_json_output(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "receipt.json"
            result = self.invoke("demo", "--out", report, cwd=directory)
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertIn("2 accepted, 2 rejected, 2 unverifiable, 0 invalid", result.stdout)
            self.assertEqual(json.loads(report.read_text())["exit_code"], 3)

    def test_sources_and_existing_reports_never_change(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source, config, output = [base / name for name in ["input.jsonl", "rules.json", "receipt.json"]]
            source.write_bytes(json.dumps(row()).encode())
            config.write_bytes(rules())
            before = source.read_bytes(), config.read_bytes()
            result = self.invoke("check", source, "--rules", config, "--out", output)
            self.assertEqual(result.returncode, 0, result.stderr)
            saved = output.read_bytes()
            self.assertEqual(self.invoke("check", source, "--rules", config, "--out", output).returncode, 4)
            self.assertEqual(output.read_bytes(), saved)
            self.assertEqual(self.invoke("check", source, "--rules", config, "--out", source).returncode, 4)
            self.assertEqual(self.invoke("check", source, "--rules", config, "--out", config).returncode, 4)
            self.assertEqual(before, (source.read_bytes(), config.read_bytes()))
            linked = base / "link.json"
            linked.symlink_to(source)
            self.assertEqual(self.invoke("demo", "--out", linked).returncode, 4)
            hardlink = base / "hardlink.json"
            os.link(config, hardlink)
            self.assertEqual(self.invoke("demo", "--out", hardlink).returncode, 4)

    def test_fatal_limits_and_usage_leave_no_report(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "report.json"
            for args in [("demo", "--max-bytes", "1"), ("demo", "--max-records", "1"),
                         ("demo", "--max-records", "0"), ("demo", "--max-bytes", "x"),
                         ("check", "missing.jsonl", "--rules", "missing.json")]:
                with self.subTest(args=args):
                    result = self.invoke(*args, "--out", out)
                    self.assertEqual(result.returncode, 4)
                    self.assertFalse(out.exists())
                    self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(self.invoke("check").returncode, 4)

    def test_byte_limit_exact_and_over(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source, config = base / "input.jsonl", base / "rules.json"
            source.write_bytes(json.dumps(row()).encode())
            config.write_bytes(rules())
            for limit, code in [(source.stat().st_size, 0), (source.stat().st_size-1, 4)]:
                self.assertEqual(self.invoke("check", source, "--rules", config, "--out", base / str(limit),
                                             "--max-bytes", limit).returncode, code)

    def test_rules_byte_limit_and_nonregular_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source, config, output = base / "input.jsonl", base / "rules.json", base / "out.json"
            source.write_bytes(json.dumps(row()).encode())
            config.write_bytes(b" " * (1024 * 1024 + 1))
            self.assertEqual(self.invoke("check", source, "--rules", config, "--out", output).returncode, 4)
            self.assertFalse(output.exists())
            config.write_bytes(rules())
            if hasattr(os, "mkfifo"):
                pipe = base / "pipe"
                os.mkfifo(pipe)
                self.assertEqual(self.invoke("check", pipe, "--rules", config, "--out", output).returncode, 4)
                self.assertFalse(output.exists())

    def test_terminal_labels_escape_control_sequences(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source, config = base / "input.jsonl", base / "rules.json"
            source.write_text(json.dumps(row(query_id="\u001b[2J")))
            config.write_bytes(rules())
            result = self.invoke("check", source, "--rules", config, "--out", base / "out.json")
            self.assertNotIn("\x1b", result.stdout)
            self.assertIn("\\u001b", result.stdout)

    def test_bundled_examples_match_repository_examples(self):
        for name in ["rules.json", "results.jsonl"]:
            self.assertEqual((ROOT / "examples" / name).read_bytes(),
                             (ROOT / "src/search_receipts/examples" / name).read_bytes())


if __name__ == "__main__":
    unittest.main()
