import base64
import hashlib
import json
import math
import platform
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import __version__

DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_RECORDS = 100_000
DEFAULT_MAX_LINE_BYTES = 1024 * 1024
MAX_RULES_BYTES = 1024 * 1024
STATES = ("accepted", "rejected", "unverifiable", "invalid")
RFC3339 = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:[Zz]|[+-][0-9]{2}:[0-9]{2})\Z"
)


class InputError(ValueError):
    pass


def normalize(text):
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


def timestamp(value):
    if not isinstance(value, str) or not RFC3339.fullmatch(value):
        return None
    if value.endswith("-00:00"):
        return None
    if value[-1:] in ("Z", "z"):
        value = value[:-1] + "+00:00"
    if value[-6:-5] in ("+", "-"):
        if int(value[-5:-3]) > 23 or int(value[-2:]) > 59:
            return None
    try:
        base, fraction = value[:19], ""
        if value[19:20] == ".":
            fraction = value[20:-6].rstrip("0")
        parsed = datetime.fromisoformat(base[:10] + "T" + base[11:] + value[-6:])
        return parsed.astimezone(timezone.utc), fraction
    except (ValueError, OverflowError):
        return None


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def _constant(value):
    raise ValueError("Non-finite JSON number: " + value)


def decode(data):
    value = json.loads(data, object_pairs_hook=_pairs, parse_constant=_constant)
    _unicode(value)
    return value


def _unicode(value):
    if isinstance(value, str):
        value.encode("utf-8")
    elif isinstance(value, dict):
        for key, item in value.items():
            _unicode(key)
            _unicode(item)
    elif isinstance(value, list):
        for item in value:
            _unicode(item)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Non-finite JSON number")


def read_bounded(path, limit):
    path = Path(path)
    if not path.is_file():
        raise InputError("Input must be a readable regular file: " + path.name)
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise InputError("File exceeds byte limit: " + Path(path).name)
    return data


def load_rules(data):
    try:
        rules = decode(data.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InputError("Invalid rules JSON: " + str(exc)) from exc
    if not isinstance(rules, dict) or set(rules) != {"schema_version", "queries"}:
        raise InputError("Rules require only schema_version and queries")
    if type(rules["schema_version"]) is not int or rules["schema_version"] != 1:
        raise InputError("Unsupported rules schema_version; expected 1")
    queries = rules["queries"]
    if not isinstance(queries, dict) or not queries:
        raise InputError("Rules need at least one query with effective criteria")
    for query_id, rule in queries.items():
        if not query_id.strip() or not isinstance(rule, dict):
            raise InputError("Each query needs a nonempty ID and a rule object")
        if set(rule) - {"description", "min_likes", "all_phrases", "any_phrases", "published_at"}:
            raise InputError("Unknown rule field for query " + query_id)
        if "description" in rule and not isinstance(rule["description"], str):
            raise InputError("description must be a string")
        active = 0
        if "min_likes" in rule:
            if type(rule["min_likes"]) is not int or rule["min_likes"] < 0:
                raise InputError("min_likes must be a nonnegative integer")
            active += 1
        for name in ("all_phrases", "any_phrases"):
            if name in rule:
                phrases = rule[name]
                if not isinstance(phrases, list) or not phrases or any(
                    not isinstance(p, str) or not normalize(p) for p in phrases
                ):
                    raise InputError(name + " must be a nonempty list of nonempty phrases")
                active += 1
        if "published_at" in rule:
            window = rule["published_at"]
            if not isinstance(window, dict) or not window or set(window) - {"from", "through"}:
                raise InputError("published_at needs from and/or through")
            if any(timestamp(value) is None for value in window.values()):
                raise InputError("Window bounds must be supported RFC3339 timestamps with known offsets")
            if "from" in window and "through" in window:
                if timestamp(window["from"]) > timestamp(window["through"]):
                    raise InputError("Window from must not follow through")
            active += 1
        if not active:
            raise InputError("Query has no effective criteria: " + query_id)
    return rules


def receipt(name, observed, rule, status, reason, **extra):
    return dict(criterion=name, observed=observed, rule=rule, status=status, reason=reason, **extra)


def combine(statuses, any_mode=False):
    if any_mode:
        if "pass" in statuses:
            return "pass"
        return "unknown" if "unknown" in statuses else "fail"
    if "fail" in statuses:
        return "fail"
    return "unknown" if "unknown" in statuses else "pass"


def evaluate(source, rule):
    if rule is None:
        return [receipt("query_rules", source["query_id"], None, "unknown", "No rules for this query_id")]
    checks = []
    if "min_likes" in rule:
        likes = source.get("likes")
        observed_at = source.get("likes_observed_at")
        problems = []
        if likes is None:
            problems.append("Likes counter is missing")
        elif type(likes) is not int or likes < 0:
            problems.append("Likes counter must be a nonnegative integer")
        if timestamp(observed_at) is None:
            problems.append("Likes observation time is missing or not a supported RFC3339 timestamp")
        if problems:
            status, reason = "unknown", "; ".join(problems)
        else:
            status = "pass" if likes >= rule["min_likes"] else "fail"
            reason = "Supplied counter meets minimum" if status == "pass" else "Supplied counter is below minimum"
            reason += "; valid only at supplied observation time"
        checks.append(receipt("min_likes", {"likes": likes, "likes_observed_at": observed_at}, rule["min_likes"], status, reason))
    for name in ("all_phrases", "any_phrases"):
        if name not in rule:
            continue
        text = source.get("text")
        complete = source.get("text_complete")
        normalized = normalize(text) if isinstance(text, str) else None
        details = []
        for phrase in rule[name]:
            found = normalized is not None and normalize(phrase) in normalized
            if found:
                status, reason = "pass", "Literal phrase is present in supplied text"
            elif normalized is not None and complete is True:
                status, reason = "fail", "Literal phrase is absent from text declared complete"
            else:
                status, reason = "unknown", "Phrase not found; supplied text is missing or not declared complete"
            details.append(dict(phrase=phrase, normalized_phrase=normalize(phrase), status=status, reason=reason))
        status = combine([d["status"] for d in details], any_mode=name == "any_phrases")
        if status == "pass":
            reason = "At least one required phrase is present" if name == "any_phrases" else "All required phrases are present"
        elif status == "fail":
            reason = "All candidate phrases are absent from text declared complete" if name == "any_phrases" else "Required phrase is absent from text declared complete"
        else:
            reason = "Missing phrases cannot be ruled out; text is missing or not declared complete"
        checks.append(receipt(name, {"text": text, "text_complete": complete}, rule[name], status, reason, phrases=details))
    if "published_at" in rule:
        original = source.get("published_at")
        parsed = timestamp(original)
        window = rule["published_at"]
        if parsed is None:
            status, reason = "unknown", "Publication time is missing or not a supported RFC3339 timestamp with known offset"
        else:
            inside = ("from" not in window or parsed >= timestamp(window["from"])) and (
                "through" not in window or parsed <= timestamp(window["through"]))
            status = "pass" if inside else "fail"
            reason = "Publication time is inside inclusive window" if inside else "Publication time is outside inclusive window"
        checks.append(receipt("published_at", original, window, status, reason))
    return checks


def row_errors(source):
    if not isinstance(source, dict):
        return ["Record must be a JSON object"]
    errors = []
    for field in ("query_id", "result_id", "url"):
        if not isinstance(source.get(field), str) or not source[field].strip():
            errors.append(field + " must be a nonempty string")
    if source.get("text") is not None and not isinstance(source["text"], str):
        errors.append("text must be a string or null")
    if source.get("text_complete") is not None and type(source["text_complete"]) is not bool:
        errors.append("text_complete must be a boolean or null")
    return errors


def check_bytes(data, rules_data, max_records=DEFAULT_MAX_RECORDS, max_line_bytes=DEFAULT_MAX_LINE_BYTES):
    rules = load_rules(rules_data)
    lines = data.split(b"\n")
    if lines[-1] == b"":
        lines.pop()
    if len(lines) > max_records:
        raise InputError("Input exceeds record limit; no partial report was written")
    rows = []
    ids = defaultdict(list)
    for number, raw in enumerate(lines, 1):
        entry = dict(line=number, query_id=None, result_id=None, classification="invalid", criteria=[], errors=[], source=None)
        if len(raw) > max_line_bytes:
            entry["errors"] = ["Line exceeds byte limit"]
        else:
            try:
                source = decode(raw.decode("utf-8"))
                entry["source"] = source
                if isinstance(source, dict):
                    for key in ("query_id", "result_id"):
                        if isinstance(source.get(key), str) and source[key].strip():
                            entry[key] = source[key]
                entry["errors"] = row_errors(source)
            except (ValueError, UnicodeError, RecursionError) as exc:
                entry["errors"] = ["Blank line" if not raw.strip() else "Invalid JSON/UTF-8: " + str(exc)]
        if entry["errors"]:
            entry["raw_base64"] = base64.b64encode(raw).decode("ascii")
        else:
            checks = evaluate(entry["source"], rules["queries"].get(entry["query_id"]))
            entry["criteria"] = checks
            status = combine([c["status"] for c in checks])
            entry["classification"] = {"pass": "accepted", "fail": "rejected", "unknown": "unverifiable"}[status]
        if entry["result_id"] is not None:
            ids[entry["result_id"]].append(number)
        rows.append(entry)
    duplicates = [{"result_id": result_id, "lines": numbers} for result_id, numbers in sorted(ids.items()) if len(numbers) > 1]
    duplicated_ids = {d["result_id"] for d in duplicates}
    for row in rows:
        row["duplicate_id"] = row["result_id"] in duplicated_ids
    groups = []
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["query_id"]].append(row)
    keys = sorted(key for key in grouped if key is not None)
    if None in grouped:
        keys.append(None)
    for key in keys:
        groups.append(dict(query_id=key, **summarize(grouped[key])))
    summary = summarize(rows)
    errors = [] if rows else ["Input is empty"]
    conditions = dict(rejections=summary["rejected"] > 0,
                      incomplete_coverage=summary["unknown_criteria"] > 0 or bool(duplicates),
                      input_errors=summary["invalid"] > 0 or bool(errors))
    exit_code = int(conditions["rejections"]) | (int(conditions["incomplete_coverage"]) << 1) | (int(conditions["input_errors"]) << 2)
    return dict(schema_version=1, tool_version=__version__,
                runtime={"python": platform.python_version(), "unicode": unicodedata.unidata_version},
                files={"input": {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)},
                       "rules": {"sha256": hashlib.sha256(rules_data).hexdigest(), "bytes": len(rules_data)}},
                effective_rules=rules,
                normalization="Unicode NFC, then casefold, then split/join Unicode whitespace; literal substring match",
                limits={"max_records": max_records, "max_line_bytes": max_line_bytes},
                summary=summary, queries=groups, duplicates=duplicates, rows=rows,
                errors=errors, conditions=conditions, exit_code=exit_code)


def summarize(rows):
    counts = Counter(row["classification"] for row in rows)
    coverage = Counter(check["status"] for row in rows for check in row["criteria"])
    return dict(total_records=len(rows), **{state: counts[state] for state in STATES},
                passed_criteria=coverage["pass"], failed_criteria=coverage["fail"], unknown_criteria=coverage["unknown"],
                rows_with_unknown=sum(any(c["status"] == "unknown" for c in row["criteria"]) for row in rows),
                duplicate_records=sum(row["duplicate_id"] for row in rows),
                distinct_result_ids=len({row["result_id"] for row in rows if row["result_id"] is not None}))
