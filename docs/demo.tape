Output docs/demo.gif
Require search-receipts
Require jq
Set Shell bash
Set FontSize 17
Set Width 1100
Set Height 640
Set Padding 24
Set Theme "Dracula"
Set TypingSpeed 25ms
Set WaitTimeout 15s
Env PS1 "$ "
Hide
Type `cd "$(mktemp -d)"`
Enter
Type `clear`
Enter
Sleep 500ms
Show
Type `search-receipts demo --out demo-report.json`
Enter
Wait+Screen /Exit 3/
Sleep 5s
Type `jq '.rows[4].criteria[1] | {status, reason, observed}' demo-report.json`
Enter
Wait+Screen /text_complete/
Sleep 7s
