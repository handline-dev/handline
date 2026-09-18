---
name: orcapipe
description: Run as one agent in an orcapipe team. Use to send a request to another agent, check your inbox, or see your handoff after a resume.
---

# orcapipe

You are one agent in a team. Your name is in `$OCP_AGENT`.

```
ocp team                       who you may ask, and what they are doing
ocp ask <target> "<request>"   send a request; the system decides if it is allowed
ocp inbox                      requests waiting for you
ocp resolve <id> "<verdict>"   answer one
ocp handoff $OCP_AGENT         what you were doing before you were resumed
```

`ocp ask` is how work reaches another agent - never spawn a subagent to do it:
that dies with your session and nobody can see it. Resolving is how a reply
reaches whoever asked, the founder included - never repeat it as a second
request. A denied ask tells you why; the policy is deliberate.

For anything else: `ocp guide` lists topics, `ocp guide <topic>` prints one.
