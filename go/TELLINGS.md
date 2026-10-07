# Tellings the Go task writes leave out

The Go write methods change the tables only: what Python does after the commit to tell an agent (a typed say, a delivery,
a push) is the delivery layer's, which Go does not have yet. Each family branch adds a line for every telling it skips, so
that when the layer exists nothing is missed. A write that tells nothing in Python has no line.

| Method | Python | What Go leaves out |
|---|---|---|
| task.memo.add | actions.memo_add, tell_memo_holder("added") | the typed say "T<n> memo added by <who>: <text>" to the holder of a doing task |
| task.memo.edit | actions.memo_edit, tell_memo_holder("edited") | the same say, "edited" |
| task.memo.delete | actions.memo_delete, tell_memo_holder("deleted") | the same say, "deleted", with no text |
