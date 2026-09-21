# Agents on an orcapipe team

True for any agent, whatever tool runs it. Your name is in `$OCP_AGENT`.

- Text typed into your own session reaches no one. Everything that has to
  reach someone goes through `ocp`.
- Work reaches another agent only through `ocp ask <target> "..."`, never a
  subagent: it dies with your session and appears on no screen.
- You hold one request at a time. It ends with `ocp resolve <id> "<verdict>"`
  (or `ocp dismiss`), which is also how a reply reaches whoever asked; the next
  request arrives only then.
- An ask can be refused; the refusal says why, and the policy behind it is
  deliberate, not a fault to route around. A name that is not on `ocp team`
  (a bare `auditor` for `orcapipe.auditor`) is refused with "unknown target" in
  the command's own output, and the refused row is kept where only the target
  can read it - so read the output, or you wait for a reply that never comes.
- A decision only the founder can make is `ocp ask human "..."`; a status
  report that needs nothing is `ocp ask human "..." --fyi`.

`ocp guide` lists the rest. Your role and standing context are injected per
agent (ROLE.md, base.md); this file does not repeat them.
