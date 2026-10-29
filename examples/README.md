# Adapting an export

Produce UTF-8 JSONL with one object per LF-delimited line. A trailing newline is
optional. Examples are fictional and use example.org; no social posts are bundled.

| Field | Type | Meaning |
| --- | --- | --- |
| `query_id` | nonempty string, required | Exact key in rules; case-sensitive |
| `result_id` | nonempty string, required | Stable source ID; duplicates flagged globally |
| `url` | nonempty string, required | Reference only; never opened or validated live |
| `text` | string or null, optional | Original supplied text, in its original language |
| `text_complete` | boolean or null, optional | True only when the source guarantees full text |
| `likes` | integer or null, optional | Counter provided by source |
| `likes_observed_at` | string or null, optional | Time that this counter was observed |
| `published_at` | string or null, optional | Publication time, if known |

Keep missing values missing. Do not infer completeness from the absence of an
ellipsis. Do not copy publication time into `likes_observed_at`: those describe
different events. Export time is an observation time only if the counter was
actually observed then. Use RFC3339 timestamps with a known offset, such as
`2026-10-29T08:00:00-04:00`. A date without an offset is not silently UTC.
Additional input fields are preserved as source metadata.

`rules.json` is versioned separately from records. Four criteria are supported:

```json
{
  "schema_version": 1,
  "queries": {
    "tools": {
      "description": "Original query label, preserved without parsing",
      "min_likes": 100,
      "all_phrases": ["need a tool"],
      "any_phrases": ["offline", "local"],
      "published_at": {
        "from": "2026-10-01T00:00:00Z",
        "through": "2026-10-31T23:59:59Z"
      }
    }
  }
}
```

Both window bounds are inclusive. Either bound can be omitted. Every configured
query must have an effective criterion. Empty phrase lists, empty windows,
unknown rule keys, and description-only rules are configuration errors. A zero
minimum is effective. Query IDs not present in the rules become unverifiable;
they are not accepted by default.

The six rows in `results.jsonl` use `tools`: two pass, one has 99 likes, one lacks
the required phrase in complete text, one has an unmatching excerpt, and one lacks
likes. `groups.jsonl` adds a `notes` row that passes with zero likes and the phrase
`local`. Concatenating both exports gives three accepted, two rejected, and two
unverifiable records; filters never move between groups.
