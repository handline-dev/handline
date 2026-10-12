---
name: orcapipe
description: Run as one agent in an orcapipe team. Use to give another agent a task, send a message, check your inbox, or see your handoff after a resume.
---

# orcapipe — you are one agent in a team, named in `$OCP_AGENT`

```
hl team                       who you may reach, and what they are doing
hl task new --assign <target> -t <time> "<title>" "<body>"   work or a question that needs an answer
hl say <target> "<message>"   a message that needs no answer
hl inbox                      the one item waiting for you, and a count
hl task done T<n> "<evidence>"   finish your task (or `hl task handoff T<n> <agent> "<note>"`)
```

The rules for any agent tool are in AGENTS.md at the repo root, the source this
skill follows. You are handed one task at a time; to change a task you gave
that is not yet done, `hl task memo T<n> add "..."`, never a correction behind it.
`hl guide` lists topics, `hl guide <topic>` prints one.
