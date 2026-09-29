---
name: orcapipe
description: Run as one agent in an orcapipe team. Use to give another agent a task, send a message, check your inbox, or see your handoff after a resume.
---

# orcapipe — you are one agent in a team, named in `$OCP_AGENT`

```
ocp team                       who you may reach, and what they are doing
ocp task new --owner <target> -t <time> "<title>" "<body>"   work or a question that needs an answer
ocp say <target> "<message>"   a message that needs no answer
ocp inbox                      the one item waiting for you, and a count
ocp task done T<n> "<evidence>"   finish your task (or `ocp task handoff T<n> <agent> "<note>"`;
                                  code: `ocp task review T<n> <sha> "<note>"`, then `ocp task land ...`)
ocp handoff $OCP_AGENT         what you were doing before you were resumed
```

The rules for any agent tool are in AGENTS.md at the repo root, the source this
skill follows. You are handed one task at a time; to change a task you gave
that is not yet done, `ocp task note T<n> "..."`, never a correction behind it.
`ocp guide` lists topics, `ocp guide <topic>` prints one.
