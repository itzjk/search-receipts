def receipt($name; $observed; $rule; $status; $reason):
  {criterion: $name, observed: $observed, rule: $rule, status: $status, reason: $reason};
def norm: ascii_downcase | split(" ") | map(select(length > 0)) | join(" ");
def status:
  if any(.[]; .status == "fail") then "rejected"
  elif any(.[]; .status == "unknown") then "unverifiable"
  else "accepted" end;
map(. as $row | $rules[0].queries[.query_id] as $rule |
  (if ($row.likes | type) != "number" then
    receipt("min_likes"; $row.likes; $rule.min_likes; "unknown"; "Counter unavailable")
  elif $row.likes < 0 or ($row.likes | floor) != $row.likes or $row.likes_observed_at == null then
    receipt("min_likes"; $row.likes; $rule.min_likes; "unknown"; "Counter or observation unavailable")
  else receipt("min_likes"; $row.likes; $rule.min_likes;
    (if $row.likes >= $rule.min_likes then "pass" else "fail" end); "Compared supplied counter") end) as $likes |
  (if ($row.text | norm | contains($rule.all_phrases[0] | norm)) then
    receipt("all_phrases"; $row.text; $rule.all_phrases; "pass"; "Phrase present")
  elif $row.text_complete == true then
    receipt("all_phrases"; $row.text; $rule.all_phrases; "fail"; "Absent from complete text")
  else receipt("all_phrases"; $row.text; $rule.all_phrases; "unknown"; "Absent from excerpt") end) as $phrase |
  {query_id, result_id, source: $row, criteria: [$likes, $phrase], classification: ([$likes, $phrase] | status)}) as $rows |
{rows: $rows, summary: ($rows | group_by(.classification) | map({key: .[0].classification, value: length}) | from_entries)}
