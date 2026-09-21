---
name: orcapipe
description: Run as one agent in an orcapipe team. Use to send a request to another agent, check your inbox, or see your handoff after a resume.
---

# orcapipe — you are one agent in a team, named in `$OCP_AGENT`

```
ocp team                       who you may ask, and what they are doing
ocp ask <target> "<request>"   send a request; the system decides if it is allowed
ocp inbox                      the one request waiting for you, and a count
ocp resolve <id> "<verdict>"   answer one
ocp handoff $OCP_AGENT         what you were doing before you were resumed
```

The rules for any agent tool are in AGENTS.md at the repo root, the source this
skill follows. You are handed one request at a time; to change a task you sent
that has not been delivered, replace it (`ocp ask <target> "..." --replaces
<id>`) or take it back (`ocp withdraw <id>`), never a correction behind it.
`ocp guide` lists topics, `ocp guide <topic>` prints one.
