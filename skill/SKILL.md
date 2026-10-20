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

A message of more than a sentence goes in lines, in Markdown; the receiver and
the app see them:

```
hl say <target> --file - <<'EOF'
T<n> done: branch x @ abc1234.
- what changed
- what was checked
EOF
```

The rules for any agent tool are in AGENTS.md at the repo root, the source this
skill follows. You are handed one task at a time; to change a task you gave
that is not yet done, `hl task comment T<n> add "..."`, never a correction behind it.
A done closes the task. To get a review, hand the task itself to the reviewer
(`hl task handoff T<n> <reviewer> "<what to check>"`); the reviewer hands it back
with findings, or on with PASS. Child tasks are for separate work, never for review.
To show the founder a screenshot, `hl say human "..." --attach shot.png` (or `hl task comment ... --attach`); a path pasted in the text shows as plain text.
`hl guide` lists topics, `hl guide <topic>` prints one.
