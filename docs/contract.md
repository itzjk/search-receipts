# Input and receipt contract, version 1

## Validation and decisions

Rules are an object with exactly `schema_version: 1` and `queries`. Each query
uses a nonempty exact string key and at least one of `min_likes`, `all_phrases`,
`any_phrases`, or `published_at`. Optional `description` is a string. Unknown rule
fields are errors to catch misspelled filters. A configuration error aborts the
run before a report is written. Rules may describe query groups absent from the
export; the report counts records actually read, not hypothetical results.

A structurally valid record has nonempty string `query_id`, `result_id`, and
`url`. Optional text must be string/null; optional completeness must be
boolean/null. Wrong types for these fields make the record `invalid`. A valid
record with unusable metric or date data remains evaluable: only the dependent
criterion is `unknown`. Malformed JSON, duplicate JSON keys, non-finite numbers,
invalid UTF-8, lone surrogate code points, non-object rows, and blank lines are
invalid records. Unknown input keys are retained. Integer likes are JSON integers,
not booleans, floats such as `100.0`, or numeric strings.

Every active criterion has `criterion`, `status`, `observed`, `rule`, and `reason`.
Phrase criteria also retain a decision and normalized phrase for each phrase.
No criterion short-circuits evaluation of another. Parent criterion coverage is
counted once, even if several phrases are involved.

| Supplied phrase evidence | Per-phrase decision |
| --- | --- |
| Match in any supplied text | pass |
| No match, text declared complete | fail |
| No match in excerpt or unknown completeness | unknown |
| Text missing, even with completeness true | unknown |

Normalization applies Unicode NFC, then Python `str.casefold()`, then `split()`
and single-space `join()` to text and phrase separately. Matching is a literal
substring test; it does not impose word boundaries, remove punctuation, apply
NFKC, stem words, or substitute synonyms. The Python/Unicode version can affect
rare characters; pin Python when comparing reports across environments.

`all_phrases` fails if any phrase fails; otherwise an unknown phrase makes the
criterion unknown; otherwise it passes. `any_phrases` passes if any phrase passes;
otherwise an unknown phrase makes the criterion unknown; otherwise it fails.

A likes criterion compares only a nonnegative integer with a valid observation
time. If either is unusable, it is unknown, including when an undated number is
below the threshold. No missing metric becomes zero. No timestamp implies
freshness or authenticity.

Publication windows compare timestamps as instants with inclusive `from` and
`through` bounds. RFC3339 with seconds and a known numeric offset or Z is
supported, including lowercase t/z and arbitrary fractional second precision.
Fractional digits are compared without rounding. Zone-less dates, impossible
calendar values, leap seconds, offsets outside 23:59, and unknown `-00:00` offsets
are unknown in records and invalid in rules. UTC conversion beyond Python's
calendar range is also unsupported. Original date values remain in the receipt.

## Counting and duplicates

A row is accepted when all active criteria pass; rejected when any fails; or
unverifiable when none fails and at least one is unknown. Missing query rules
produce a `query_rules` unknown receipt. Invalid rows are counted separately.
A reject with unknown criteria remains rejected and contributes to unknown
coverage. The four counts always partition `total_records`, globally and for
each query. A null query summary holds rows whose query ID could not be read;
it cannot collide with a user-supplied string ID.

`distinct_result_ids` counts known IDs only, never people. Duplicate IDs are
matched globally, even across query groups and structurally invalid records
whose IDs are readable. All occurrences retain their own counters and line
numbers. `duplicate_records` counts every flagged occurrence. The `duplicates`
array lists each repeated ID and all its lines. Duplication signals incomplete
coverage but does not change per-row criterion classifications. No deduplication,
aggregation of likes, precision, recall, or demand estimate is performed.

## Report and file handling

JSON output carries `schema_version: 1`, tool and Python/Unicode versions, SHA-256 and byte length of
both exact file snapshots, effective rules, normalization, limits, rows, query
summaries, overall summary, errors, conditions, and exit code. Each row has its
physical 1-based line number, IDs, source object, classification, errors,
criteria, and duplicate flag. Invalid lines include `raw_base64` so their exact
bytes can be recovered without lossy UTF-8 replacement. Valid rows retain all
JSON values; original file formatting is represented by the input hash.

Limits default to 10 MiB input, 1 MiB per line, 100,000 records, and 1 MiB rules.
Every LF-delimited physical line is a record, including blank lines. A final LF
terminates the preceding line; it adds no extra blank record. CRLF is accepted.
An empty file contains zero records and is an input error, with an empty report.
Oversized lines count as invalid; their bytes remain in the report. Whole-file
byte limits, record limits, unreadable inputs, invalid rules, and existing or
unwritable output paths are fatal: exit 4 with no new report. Such errors do not
claim that unread/unprocessed records were audited. Nonregular inputs, including named pipes and device files, are refused before
reading.

Inputs are opened read-only. Reports are exclusively created at a new path;
existing files, symlinks, and hardlinks cannot be overwritten. A filesystem
write failure may leave an incomplete new report; a report is complete only when
the command finishes with its reported result code. Reports include source text,
so handle them as carefully as the input exports.

## Exit conditions and precedence

A completed run sets independent flags, then combines them with bitwise OR:

| Flag | Condition |
| --- | --- |
| 1 | One or more rejected records |
| 2 | One or more unknown criteria or duplicate IDs |
| 4 | One or more invalid records, or empty input |

Thus 0 means all verifiable/accepted; 1 only rejection; 2 only incomplete
coverage; 3 both; 4 only input errors; 5 input errors and rejections; 6 input
errors and incomplete coverage; 7 all three. Fatal setup/read/limit errors take
precedence and return 4 without a report. Usage errors also return 4; help and
version return 0. Human output shows per-query counts and up to eight problem
reasons; the JSON contains every reason. URLs are data and are never opened.
