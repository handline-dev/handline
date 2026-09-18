---
name: orcapipe
description: Run as one agent in an orcapipe team. Use to send a request to another agent, check your inbox, or see your handoff after a resume.
---

# orcapipe — you are one agent in a team, named in `$OCP_AGENT`

```
ocp team                       who you may ask, and what they are doing
ocp ask <target> "<request>"   send a request; the system decides if it is allowed
ocp inbox                      requests waiting for you
ocp resolve <id> "<verdict>"   answer one
ocp handoff $OCP_AGENT         what you were doing before you were resumed
```

`ocp ask` is how work reaches another agent - never a subagent, which dies with
your session and appears on no screen. `ocp resolve` is how a reply reaches
whoever asked, the founder included - never a second request repeating it. A
denied ask tells you why; the policy is deliberate. For anything else:
`ocp guide` lists topics, `ocp guide <topic>` prints one.
