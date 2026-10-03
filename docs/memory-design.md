# Agent memory (T229)

Core is built (T231: `ocp memory status|migrate`, the doctor check, `agents/<name>/memory`
on register, the team-layer file, the `AGENTS.md` promotion). The daemon part
(T230: launch setting, hooks) is not. Founder #1272-#1274: each agent keeps its
own memory and works from it, and memory must not pile up ("too much is worse
than none").

## Today

- Claude Code keys auto-memory by repo: `<config dir>/projects/<repo>/memory/`.
  ocp links that folder from every account to `shared/memory/claude/<repo>`
  (`accounts.share_memory`, retired in T246), so the six orcapipe agents share one folder.
- It holds 13 topic files and a `MEMORY.md` that lists **6** of them. Claude
  loads only the index at start, so the other 7 are never loaded unless an
  agent happens to open them. Two files (`ocp-cli-habits`, `ocp-resolve-quoting`)
  say the same thing; three UI-testing files overlap.
- `agents/<name>/journal` is unused. `base.md` (2KB) and `ROLE.md` are injected
  as the system-prompt appendix on every launch (`launch.context_for`).

## Layers

| Layer | Where | Loaded into | Holds |
|---|---|---|---|
| Repo rules | `AGENTS.md` (PR-reviewed) | every agent in that repo | rules true for all |
| Team | `~/.orcapipe/shared/team/<team>.md` | every agent of the team, in the appendix beside `base.md` | at most 10 rules, 2KB |
| Agent | `~/.orcapipe/agents/<name>/memory/` | that agent only | its `MEMORY.md` index + topic files |

The team layer is one injected file, not a shared memory folder: Claude loads a
single memory dir per session, and once the agent dir is set the repo dir is no
longer loaded at all.

## How a session is pointed at its own memory

Checked against the Claude Code docs (code.claude.com/docs/en/memory, fetched
2026-09-27). Not checked against the installed binary (2.1.283): this sandbox
cannot read it, so the daemon task starts with one live launch (see Tasks).

| Option | Verdict |
|---|---|
| `autoMemoryDirectory` in settings | **Use this.** Read from any scope, including `--settings`; absolute path or `~/`. Replaces the repo-keyed dir for that session. |
| `CLAUDE_CODE_PROJECT_DIR_NAME` (env, v2.1.234+) | No. It renames the whole project dir, so transcripts move too, and `handoff`, `collect` and `tokenlog` find sessions there. |
| `CLAUDE_CONFIG_DIR` per launch | Already one per account, not per agent; changing it would break account moves. |
| Inject memory through `base.md` or a hook | Only for the team layer. It cannot make Claude *write* to the right place. |

`launch_argv` already passes one `--settings` JSON (the `hide` deny list); the
driver merges `autoMemoryDirectory` into that same object. A new
`Driver.memory_settings(dir)` returns it, and opencode (no auto-memory) returns
nothing. Because the dir is keyed by agent, an account move or a worktree
rename no longer touches memory: `share_memory`, `migrate_memory` and the
memory half of `rehome` go away for agent memory.

Isolation: extend the `hide` list with every *other* agent's memory dir, the
same way other agents' transcripts are hidden now, and make sure the sandbox's
home-write allowance covers the agent's own dir.

Also to confirm live: Claude does not load auto memory from a dir named in
repository-supplied settings while `blockReadsOutsideWorkingDirectories` is on.
`--settings` is not repository-supplied, but the launch check should read the
index back to prove it loaded.

## Keeping it small

Budget per agent: `MEMORY.md` at most **2KB and 20 entries**. Claude's own
limit (200 lines / 25KB) is 10x looser and is never reached.

1. **Cap, enforced deterministically.** A PreToolUse hook on Write/Edit of a
   file under the agent's memory dir denies a change that makes an over-budget
   `MEMORY.md` bigger, with the reason "merge or delete before adding". Shrinking
   edits pass. No model call.
2. **Last use.** A PostToolUse hook writes `agents/<name>/memory-use.json`
   (`{file: last_ts}`) for a Read, Edit, Write or MultiEdit under the memory
   dir; outside it, or for another tool, it exits at once. The file lives
   outside the memory dir so it is never loaded or counted. The filtering is
   `memoryguard.note_use`'s own (`USE_TOOLS`), not a Claude-side matcher on the
   installed hook: PostToolUse fires unmatched on every tool call because it is
   also how a permission-wait clears (T247, waiting.py) once the agent's turn
   continues past *any* tool, not only Read/Edit - narrowing the matcher would
   fix nothing here and silently stop that clearing for every other tool.
3. **Pruning candidates.** `hl doctor` and `hl memory status` list per agent:
   bytes and entries against budget, files not in the index, and files unused
   for 28 days. Candidates are reported, never deleted by ocp.
4. **Consolidate at handoff.** The handoff record is built by the daemon with no
   model, so it cannot ask the agent anything; instead it carries one `memory:`
   line (bytes, entries, unindexed and unused files, "merge, promote or delete
   these before you finish") when the memory needs tending, and a fresh session
   with no record gets the same line in its wake prompt. Built in T230.
5. **Promotion.** A lesson every agent needs moves to `AGENTS.md` / `ROLE.md`
   by PR and is deleted from memory; `hl memory status` flags an entry whose
   text also appears in another agent's memory as a promotion candidate.
6. **No automatic summarising.** ocp never rewrites an entry; rules stay verbatim.

Known gap: an entry the agent applies straight from its index line never
triggers a `Read`, so its "last use" looks stale. Rule: the index line is a
pointer of at most 100 characters and the rule lives in the topic file, so
applying it means reading it. Candidates are a prompt for the agent's judgment
at handoff, not a verdict.

## Migrating the 13 topic files (owner inferred from content)

| Files | To | Note |
|---|---|---|
| `batch-per-owner`, `idle-not-free`, `feedback-queue-discipline`, `founder-reports-fyi`, `feedback-founder-working-style` | `orcapipe` | 5 entries |
| `auditor-run-suite-before-verdict` | `orcapipe.auditor` | |
| `feedback-testing` + `feedback-minimal-ui-tests` + `feedback-full-suite-before-review` | `orcapipe.ui`, merged into one | 3 to 1 |
| `feedback-no-tests-until-ok` | `orcapipe.ui`, or delete | founder said "until OK" on 2026-09-26: ui asks whether it still stands |
| `ocp-cli-habits` + `ocp-resolve-quoting` | `AGENTS.md`, single-quote rule | 2 to 1, leaves memory |
| `project-ocp-home-wipe` | delete | fixed in 09dc7c1 and guarded in `paths`; git records it |

Result: 13 files become 9 entries (orcapipe 5, ui 2, auditor 1, AGENTS.md 1);
3 duplicates merge away and 1 is deleted. core, daemon and merger start empty. `AGENTS.md` currently says to run the whole suite with
`tools/suite.sh` while the ui entries say "full suite only at the auditor gate";
reconcile the wording when promoting. `hl memory migrate` runs as a dry run
first, writes each owner's dir and index, and leaves `shared/memory` untouched
until each agent has confirmed its own entries once; then the links are removed.

## Tasks

- **daemon:** first, one live launch with `--settings '{"autoMemoryDirectory": ...}'`
  proving the index loads and writes land there; `Driver.memory_settings` and the
  merged `--settings`; hide other agents' memory dirs; the two hooks; the
  handoff and wake wording.
- **core:** `hl memory status|migrate`; `hl doctor` check; create
  `agents/<name>/memory/` on register; remove the account-move memory code for
  agents; the team-layer file and its injection next to `base.md`; the
  `AGENTS.md` promotion PR.
- **ui:** a read-only memory line per agent (bytes and entries against budget,
  candidates with last use). Shape agreed with the founder before it is built.

## Open for the founder - decided (#1287)

1. **Team layer: a separate file** in the appendix, `shared/team/<team>.md`,
   injected beside `base.md` for each team the agent is in. `hl doctor` warns
   past 10 rules or 2KB.
2. **Pruning candidates: 28 days** (`memory.STALE_DAYS`).
3. **Over budget, the write is denied** and the reason shown (T230's
   PreToolUse hook); it is not a warning.

## Built by core (T231)

- `hl memory status [agent]`: bytes and entries against budget, files not in
  the index, files unused 28+ days (last use from `memory-use.json`, the file's
  mtime for one never read), and identical text in two agents' memory as a
  promotion candidate.
- `hl memory migrate`: dry run; `--apply` copies each topic file verbatim to
  its owner's `agents/<name>/memory/` with a pointer line in `MEMORY.md`, never
  overwriting. Merges (3 to 1) are the owner's to do; ocp does not summarise.
  `shared/memory` is never touched: the links come off after each agent has
  confirmed its entries, by hand.
- Contract with T230: memory dir `agents/<name>/memory/`, sidecar
  `agents/<name>/memory-use.json` = `{path relative to the memory dir: unix
  seconds}`, budget constants and `memory.status()` in `handline/team/memory.py`.
