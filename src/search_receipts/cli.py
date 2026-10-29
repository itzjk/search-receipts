import argparse
import json
import sys
from importlib.resources import files
from pathlib import Path

from . import __version__
from .core import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINE_BYTES,
    DEFAULT_MAX_RECORDS,
    MAX_RULES_BYTES,
    InputError,
    check_bytes,
    read_bounded,
)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError(message)


def positive(value):
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected a positive integer") from exc
    if result < 1:
        raise argparse.ArgumentTypeError("Expected a positive integer")
    return result


def parser():
    root = Parser(description="Check exported search rows and keep every decision receipt.")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True, parser_class=Parser)
    check = commands.add_parser("check", help="Check a local JSONL export against explicit rules")
    check.add_argument("results", type=Path)
    check.add_argument("--rules", required=True, type=Path)
    demo = commands.add_parser("demo", help="Check the bundled six-row synthetic example")
    for command in (check, demo):
        command.add_argument("--out", required=True, type=Path, help="New JSON report path; existing files are refused")
        command.add_argument("--max-bytes", type=positive, default=DEFAULT_MAX_BYTES)
        command.add_argument("--max-records", type=positive, default=DEFAULT_MAX_RECORDS)
        command.add_argument("--max-line-bytes", type=positive, default=DEFAULT_MAX_LINE_BYTES)
    return root


def human(report):
    summary = report["summary"]
    print("search-receipts | {} records".format(summary["total_records"]))
    for group in report["queries"]:
        label = json.dumps(group["query_id"], ensure_ascii=True)
        print("{}: {} accepted, {} rejected, {} unverifiable, {} invalid".format(
            label, group["accepted"], group["rejected"], group["unverifiable"], group["invalid"]))
    issues = []
    for row in report["rows"]:
        for error in row["errors"]:
            issues.append((row["line"], "invalid", error))
        for check in row["criteria"]:
            if check["status"] != "pass":
                issues.append((row["line"], check["status"], check["criterion"] + ": " + check["reason"]))
    for line, status, reason in issues[:8]:
        print("  line {} [{}] {}".format(line, status, json.dumps(reason, ensure_ascii=True)))
    if len(issues) > 8:
        print("  {} more reasons in the JSON report".format(len(issues) - 8))
    for error in report["errors"]:
        print(error)
    print("Coverage: {} pass, {} fail, {} unknown criteria; {} duplicate ID(s)".format(
        summary["passed_criteria"], summary["failed_criteria"], summary["unknown_criteria"], len(report["duplicates"])))
    print("Exit {} | report saved".format(report["exit_code"]))


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        if args.out.exists() or args.out.is_symlink():
            raise InputError("Output already exists; choose a new report path")
        if args.command == "demo":
            resource = files("search_receipts").joinpath("examples")
            data = resource.joinpath("results.jsonl").read_bytes()
            rules_data = resource.joinpath("rules.json").read_bytes()
            if len(data) > args.max_bytes:
                raise InputError("Demo input exceeds byte limit")
        else:
            data = read_bounded(args.results, args.max_bytes)
            rules_data = read_bounded(args.rules, MAX_RULES_BYTES)
        report = check_bytes(data, rules_data, args.max_records, args.max_line_bytes)
        report["limits"].update(max_bytes=args.max_bytes, max_rules_bytes=MAX_RULES_BYTES)
        encoded = (json.dumps(report, ensure_ascii=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
        with args.out.open("xb") as handle:
            handle.write(encoded)
        human(report)
        return report["exit_code"]
    except (InputError, OSError, ValueError, RecursionError) as exc:
        print("search-receipts: " + str(exc), file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
