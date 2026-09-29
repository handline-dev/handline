# Agents on an orcapipe team

True for any agent, whatever tool runs it. Your name is in `$OCP_AGENT`.

- Text typed into your own session reaches no one. Everything that has to
  reach someone goes through `ocp`.
- There is one kind of message, `ocp say <target> "..."`: typed at once, needs
  no answer, starts no work. Anything that needs an answer or a result is a
  task: `ocp task new --owner <target> -t <time> "<title>" "<body>"`. Work
  reaches another agent only as a task, never a subagent: it dies with your
  session and appears on no screen.
- You hold one task at a time. It ends with `ocp task done T<n> "<evidence>"`,
  or you hand it on with `ocp task handoff T<n> <agent> "<note>"` - that is how
  the result reaches whoever asked, never a second task repeating it. The next
  task arrives only then.
- A task or a say can be refused; the refusal says why, and the policy behind
  it is deliberate, not a fault to route around. A name that is not on
  `ocp team` (a bare `auditor` for `orcapipe.auditor`) is refused with "unknown
  target" in the command's own output - read it, or you wait for a reply that
  never comes.
- A manager cannot hand a report work by message: a say to a report carries
  `--task T<n>` (a note on that task) or `--fyi "<why it is not work>"`.
- A decision only the founder can make is a task of theirs: `ocp task new
  --owner human ...`. A status report that needs nothing is `ocp say human
  "..."`.

## Hard rules

1. Never read, write or copy a credential: `.credentials.json`, the Keychain,
   `.claude.json`. Adding an account is one `CLAUDE_CONFIG_DIR=<dir> claude /login`
   run by a person; a refresh token rotates once, so copies invalidate each other.
   The one exception (#2010): a driver's meter path may read an opencode
   `auth.json` in memory, once, for the usage question only. The key never reaches
   an agent's env, prompt, terminal, log, db or any file ocp writes.
2. Do not code on an assumption nobody verified. Probe it first.
3. No logic in `SKILL.md`: it is a stub of at most 20 lines, and detail loads on
   demand through `ocp guide <topic>`.
4. No model in orchestration. Triggers, account picks and resume decisions are
   deterministic and the daemon makes them. The one place a model spends tokens is
   a fold.
5. Never dump `--json` output whole into a log or the db (a token leak path):
   take the fields with a reader, by allowlist.
6. Do not finish a phase without `ocp doctor` checking its preconditions.

Easy to get wrong:

- Count a transcript once per API response (`requestId`), never per line: one
  response is several JSONL lines repeating its usage (`token_calls` is keyed on it).
- A fold runs outside the session, so its input does not grow with the session.
- A fold's cursor is a row id, never a time, and moves only after the output
  validates (schema, and nothing `done` before is gone).
- Triggers are level triggers: an edge trigger misses a crossing that happened
  while the daemon was down.
- Everything under `~/.orcapipe` is writable by every agent, so it cannot prove
  the founder asked for something. What only the founder may grant (an agent's
  `skip_permissions`, the declared trust roots, the founder's answer to a roster
  or trust proposal) is kept in `ocp/infra/protected.py`, outside it. The
  proposal drafts stay in `~/.orcapipe`: an agent writes those, so only the
  recorded answer counts.

`ocp guide` lists the rest. Your role and standing context are injected per
agent (ROLE.md, base.md); this file does not repeat them.

## How we work

- Tests: when you change code, write unit tests for the change in the same
  commit. Work is not done until they pass. While iterating, run only the
  tests you touched (`python3 tests/test_ocp.py -k <name>`); run the whole
  suite once, right before you hand the task to `orcapipe.auditor`, with `tools/suite.sh`, in
  the foreground with a 600000 ms Bash timeout, never in the background. The
  auditor reruns it at the exact sha; a count you quote is not a verdict.
- UI: don't design a screen alone. Agree on its shape with the human first,
  and after you build it, the human checks it by eye. Passing tests do not
  finish UI work.
- Language: agents write to each other, write tasks, code and comments in
  English. Only a reply to the founder may be in Korean.
- Run `ocp` as its own Bash call with literal text: no `$(...)`, no `$VAR`, no
  `&&`, `;` or pipe. A value such as a sha comes from a separate call first.
- Permission prompts: edit files with the Edit tool, never `sed -i`, which can
  write any file and so always prompts. Don't start a command with `cd`; use
  paths or `git -C` instead.
- Text to `ocp say`, `ocp task new` and `ocp task handoff` goes in single quotes
  (a literal `'` as `'"'"'`): in double quotes the shell runs backticked words
  and they vanish from the message.
