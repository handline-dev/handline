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

`ocp ask` is how work reaches another agent - never a subagent, which dies with
your session and appears on no screen. `ocp resolve` is how a reply reaches
whoever asked, the founder included - never a second request repeating it. A
denied ask tells you why; the policy is deliberate.

You are handed one request at a time, the head of your queue - not the whole
of it - and the next only arrives once you resolve or dismiss this one. To
change a task you sent that has not been delivered yet, replace it
(`ocp ask <target> "..." --replaces <id>`) or take it back (`ocp withdraw
<id>`); do not send a correction behind it. A question or decision for the
founder is `ocp ask human "..."` and shows red until answered; a status report
that needs nothing is `ocp ask human "..." --fyi`, which does not. For anything else: `ocp guide`
lists topics, `ocp guide <topic>` prints one.
