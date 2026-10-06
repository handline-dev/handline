# Agents on an orcapipe team

True for any agent, whatever tool runs it. Your name is in `$OCP_AGENT`.

- Text typed into your own session reaches no one. Everything that has to
  reach someone goes through `hl`.
- There is one kind of message, `hl say <target> "..."`: typed at once, needs
  no answer, starts no work. Anything that needs an answer or a result is a
  task: `hl task new --owner <target> -t <time> "<title>" "<body>"`. Work
  reaches another agent only as a task, never a subagent: it dies with your
  session and appears on no screen.
- You hold one task at a time. It ends with `hl task done T<n> "<evidence>"`,
  or you hand it on with `hl task handoff T<n> <agent> "<note>"` - that is how
  the result reaches whoever asked, never a second task repeating it. The next
  task arrives only then.
- Review and merge (orcapipe's project guideline; `hl team` names your own
  project's auditor and merger) are child tasks you wait on, not handoffs: you
  keep your task, in doing. When code is ready, `hl task new --owner <auditor>
  --parent T<n> -t <time> "review T<n> @<sha>" "<branch, what changed, what to
  check>"`; the child blocks T<n>, and the auditor closes it with `hl task done
  T<child> "PASS: <notes>"` or `"FAIL: <what fails>"` - you are told when it
  is done, with its result. On a PASS open the same for `orcapipe.merger`
  (`"merge T<n> @<sha>"`, body naming the PASS task); it closes with the merged
  commit sha or `"not merged: <reason>"`. Then mark T<n> done with that sha
  (`hl task done T<n> <sha>`; it is refused while a child is open), else
  `hl task handoff T<n> <its opener> "merged <sha>"`. A wrong result is
  reopened by its asker. An auditor or merger never hands a review or merge task
  back: it finishes it.
- A task or a say can be refused; the refusal says why, and the policy behind
  it is deliberate, not a fault to route around. A name that is not on
  `hl team` (a bare `auditor` for `<team>.auditor`) is refused with "unknown
  target" in the command's own output - read it, or you wait for a reply that
  never comes.
- A manager cannot hand a report work by message: a say to a report carries
  `--task T<n>` (a memo on that task).
- A decision only the founder can make is a task of theirs: `hl task new
  --owner human ...`. A status report that needs nothing is `hl say human
  "..."`.

## Hard rules

1. Never read, write or copy a credential: `.credentials.json`, the Keychain.
   Never write or copy `.claude.json`. Adding an account is one
   `CLAUDE_CONFIG_DIR=<dir> claude /login` run by a person; a refresh token rotates
   once, so copies invalidate each other. Two exceptions: a driver's meter
   path may read an opencode `auth.json` in memory, once, for the usage question
   only; `Driver.profile` and `Driver.login_id` in the claude driver are the only readers of
   `.claude.json`: the first returns only the subscription start and the two rate-limit
   tier strings, the second only a one-way fingerprint of the account id, which keys the
   stored email, so it is asked of claude only when the login is new. The founder:
   "Go with A. Reading is fine. Just do not expose a token or the like that you read."
   Read is allowed; exposing a token, key or other secret from it is not. No key,
   token, email or other value from either file reaches an agent's env, prompt,
   terminal, log, db or any file ocp writes.
2. Do not code on an assumption nobody verified. Probe it first.
3. No logic in `SKILL.md`: it is a stub of at most 20 lines, and detail loads on
   demand through `hl guide <topic>`.
4. No model in orchestration. Triggers, account picks and resume decisions are
   deterministic and the daemon makes them. The one place a model spends tokens is
   a fold.
5. Never dump `--json` output whole into a log or the db (a token leak path):
   take the fields with a reader, by allowlist.
6. Do not finish a phase without `hl doctor` checking its preconditions.
7. Nothing the repo keeps (commit messages, code, comments, tests, docs) carries personal
   data or an id that only means something on this machine: no email, real name, home
   path (`/Users/<name>`), token or key; no task id (`T<n>`), chat message number (`#<n>`)
   or internal agent name. Write the why in words instead, so a stranger reading the
   repo can follow it. The task board keeps the ids; `tools/land.sh --note` records a
   landing's ids locally, outside git. A real need (a fixture that must hold one) gets
   `local-ref-ok` on its line, or its file an entry in `tools/localrefs.allow`. In a Markdown
   file an id that is an argument of an `hl` command is the product's own syntax and passes.
   A commit's author and committer email must be a noreply address too
   (`git config user.email <id>+<name>@users.noreply.github.com`).
   `tools/localrefs.py` enforces it in `tools/land.sh` and in the git hooks
   (`tools/install-git-hooks.sh`), and a review runs it too:
   `tools/localrefs.py diff main...<sha>` and `tools/localrefs.py log main..<sha>`.

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
  or trust proposal) is kept in `handline/infra/protected.py`, outside it. The
  proposal drafts stay in `~/.orcapipe`: an agent writes those, so only the
  recorded answer counts.

`hl guide` lists the rest. Your role and standing context are injected per
agent (ROLE.md, base.md); this file does not repeat them.

## How we work

- Tests: when you change code, write unit tests for the change in the same
  commit. Work is not done until they pass. While iterating, run only the
  tests you touched (`tools/test.sh -k <name>`, or `tools/test.sh tests/<group>/test_<area>.py`); run the whole
  suite once, right before you hand the task to `orcapipe.auditor`, with `tools/suite.sh`, in
  the foreground with a 600000 ms Bash timeout, never in the background. The
  auditor reruns it at the exact sha; a count you quote is not a verdict.
- Run tests only through `tools/test.sh`, never `python3 tests/...` by hand: it brings the
  venv python, an unset `OCP_AGENT`, unbuffered output and a temp `OCP_HOME` (bridge tests
  print nothing without them, and a hang looks like a stall).
- TypeScript: run `pnpm turbo test typecheck lint` before you hand a task over. Aim for files under 250 lines and
  functions under 60; lint fails at 400 and 120 (tests 600, components 250), counted without blank or comment lines.
  A `max-lines` error means split the file by responsibility, not raise the limit or add a disable. The numbers are in
  `packages/config/eslint/limits.js`.
- UI: don't design a screen alone. Agree on its shape with the human first,
  and after you build it, the human checks it by eye. Passing tests do not
  finish UI work.
- Language: agents write to each other, write tasks, code and comments in
  English. Only a reply to the founder may be in Korean.
- Run `hl` as its own Bash call with literal text: no `$(...)`, no `$VAR`, no
  `&&`, `;` or pipe. A value such as a sha comes from a separate call first.
- Permission prompts: edit files with the Edit tool, never `sed -i`, which can
  write any file and so always prompts. Don't start a command with `cd`; use
  paths or `git -C` instead.
- Text to `hl say`, `hl task new` and `hl task handoff` goes in single quotes
  (a literal `'` as `'"'"'`): in double quotes the shell runs backticked words
  and they vanish from the message. Text with backticks or `$(`, or with a literal `'`
  in it, goes in a quoted heredoc on stdin as `-`, or in a file as `--file PATH`
  (`hl say`, `hl task new`, `hl task handoff`, `hl task memo add`).
