# Why keep a dedicated receipt?

## Reproduce before building

On 2026-10-29, jq 1.7.1 was run against the six synthetic records before the
Python implementation was written:

```sh
jq -s --slurpfile rules examples/rules.json -f docs/baseline.jq examples/results.jsonl > baseline.json
jq '.summary' baseline.json
```

The observed result, also saved in `baseline-output.json`, is:

```json
{"accepted": 2, "rejected": 2, "unverifiable": 2}
```

The baseline preserves every source row, query/result ID, observed value, applied
rule, criterion outcome, and reason. jq can do more than discard rows; this
experiment makes no claim that a Python package is necessary to compute the
matrix. The [jq manual](https://jqlang.org/manual/) documents its filter and
transformation primitives.

## What the experiment did and did not cover

| Property | Executed jq baseline | search-receipts contract |
| --- | --- | --- |
| Six-row matrix | 2 / 2 / 2 | 2 / 2 / 2 |
| Evidence retained | Source, IDs, two criteria and reasons | Source, IDs, all active criteria and reasons |
| Configuration | JSON rules plus case-specific jq program | Versioned JSON with four criterion types |
| Scope tested | One query, one all-phrase, ASCII examples | Independent queries, all/any phrases, Unicode and dates |
| Missing observation time | Unknown | Unknown, with strict timestamp validation |
| Invalid physical lines | jq aborts | Counted invalid with recoverable bytes |
| Other audit metadata | Not implemented | Line numbers, duplicates, hashes, coverage, combined exit flags |

The jq program deliberately covers only this matrix. Its ASCII lowercase/space
normalization, single phrase, missing timestamp validation, and assumed rule
shape are insufficient for the full contract. Extending it remains a valid
choice if you already maintain jq workflows. The dedicated CLI earns its scope
by bundling the receipt contract, validation, and accounting, rather than by
inventing a new filtering technique.

[Great Expectations](https://docs.greatexpectations.io/docs/core/introduction/)
provides general data-validation workflows; it was reviewed through its official
introduction, not installed or benchmarked here. [csvgrep](https://csvkit.readthedocs.io/en/latest/scripts/csvgrep.html)
filters CSV rows. Neither review establishes absence of equivalent custom
solutions, market superiority, or demand for this package. For a single predicate
and no audit requirements, an existing filter may be all you need.

## Evidence boundary

The project brief describes query-label and saved-row discrepancies in a private
export. Its underlying posts and business data are not redistributed here, and
that audit is not independently remeasured in this repository. The public
reproduction uses fictional records to show the distinction between a known
mismatch and missing evidence. It demonstrates the data-quality case, not votes
for this product or a finding about any provider's correctness.

[X's official query guide](https://docs.x.com/x-api/posts/search/integrate/build-a-query),
read on 2026-10-29 (no publication date shown on the page), documents operators
and grouping for X API search. That does not establish what another search
provider or exporter supports. Query interpretation, extraction, and counter
observation timing are possible causes of a discrepancy, not established causes.
The CLI therefore treats the original query as provenance and checks only
explicit user rules. The documentation pages above were read on the same date;
the jq runtime result is independently reproducible from the bundled files.
