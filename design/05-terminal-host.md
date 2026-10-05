# 05 - The terminal host

Design only, no code. Question (founder): make the Orca part a swappable interface, like `handline/vendors/`.

**Most of that is already built.** `handline/session/backends/` holds a `Backend` base (`base.py`), `OrcaBackend` (`orca_cli.py`) and `TmuxBackend` (`tmux.py`). `OCP_TERMINAL` or `terminal:` in config.yaml picks one (`backends/__init__.py`). `tests/infra/test_terminal_backends.py` runs the terminal layer on a `FakeHost`, pins Orca's exact CLI calls, and has a guard test that no module outside `backends/` starts an `orca` process. So the work left is not "introduce an interface" but "close what still goes around it, then add a third host".

## 1. What handline asks of the host today

Eighteen of Orca's 236 verbs, all run by `OrcaBackend`; three of them (`worktree show`, `worktree set`, `terminal list`) are also asked by callers as raw `host_cli` text.

| Group | Orca verb | `Backend` method |
|---|---|---|
| terminals | `terminal create` | `create_terminal(command, cwd, title)` |
| | `terminal close --tab` | `close_terminal(handle, tab)` |
| | `terminal list` | `list_terminals() -> set \| None` |
| | `terminal read --screen` | `read_screen(handle, limit)` |
| | `terminal send [--enter] [--wait-submit]` | `send(handle, text, enter, wait_submit_s)` |
| | `terminal wait --for tui-idle` | `wait_idle(handle, timeout_ms)` |
| | `terminal switch` | `focus(handle)` |
| identity | env `ORCA_TERMINAL_HANDLE`, `ORCA_WORKTREE_ID` | `own_terminal()` |
| repos | `repo list`, `repo add` | `ensure_repo(path, present)` |
| worktrees | `worktree create` | `create_worktree(repo, name, base_ref)` |
| | `worktree show`, `worktree set` (label, comment, parent, status), `terminal list` read for `worktreePath` | **`host_cli(args)`**: raw verbs, no typed method |
| the team card | `orchestration run-create / run-current / run-show / run-use / task-create / dispatch` | `run_*`, `task_create`, `dispatch` (only if `supports_runs`) |

Callers reach all of it through `session/orca.py`, which 30 files import. `orca.available()` alone is called 30 times; `alive` 9, `wait_idle` 6, `host_name` 6, `host_cli` 6.

## 2. The interface

Keep `Backend` as the interface. Its rules already fit what a vendor-style seam needs: it knows nothing of agents, vendors or dialogs; every method answers rather than raises; `None` is "could not ask", never "nothing there"; optional abilities are flags (`supports_runs`, `supports_labels`).

What leaks past it, and what closes each:

1. **Raw Orca JSON outside the backend.** `host_cli` hands `team/hr.py:887,915` and `team/rehome.py:114,127,139,196` Orca's own reply shape (`displayName`, `childWorktreeIds`, `parentWorktreeId`, `workspaceStatus`, the `selector_not_found` code, a terminal's `worktreePath`/`worktreeId`). Replace with typed methods: `worktree_info(path) -> WorktreeInfo | None | Unreadable`, `set_worktree(path, label=, comment=, parent=, status=)`, `terminals_in(path) -> list[str] | None`. `host_cli` is then deleted. Both callers still do nothing on a host with `supports_labels` False.
2. **Orca commands printed as advice.** `cli/agent.py:87`, `team/guide.py:23`, `team/spawn.py:173-180` (already host-conditional), `team/rehome.py:147,161,175`, `team/hr.py:909`. Each is "what to type by hand when the host would not do it". Add `Backend.manual(op, **args) -> str` so the host words its own fallback, like `ensure_repo` already returns what to tell the founder.
3. **Name checks instead of capabilities.** `host_name() == "orca"` guards the doctor's hook check (`infra/doctor.py:133`) and the spawn dry run. Add `supports_agent_hooks` (Orca installs hooks into `~/.claude/settings.json` that `StateHooks` mirrors; tmux has none and the agent's own `hl hook` entries are the witness). `host_name()` then only prints.
4. **Hard-coded "orca is not on PATH"** in `daemon/delivery.py:201`, `daemon/messaging.py:33`, `actions/control.py:154`, though `orca.missing()` exists. `session/blocked.py` says "orca down" (`ORCA_DOWN`). Use `missing()`; rename the blocker to "host down".
5. **A vendor's wording inside a backend.** `tmux.py` `WORKING_MARKERS = ("esc to interrupt",)` is claude's status row, used to decide idle. `wait_idle` should take the driver's `working_on_screen` (it exists, `vendors/base.py`) so a second vendor on tmux works. Orca's own `tui-idle` is a host-side witness that the driver already treats separately (`idle_from_screen`).
6. **The module's name.** `session/orca.py` is the host-neutral terminal layer (dialogs, idle, clear, close, relaunch, 1,439 lines) named after one host. Rename to `session/term.py`, leaving `orca.py` as a shim, the way `ocp` became `handline` (4a4e12a1).

## 3. Migration, Orca working at every step

Every step keeps `test_the_orca_backend_makes_the_same_cli_calls_it_always_did` green, which is the proof Orca saw the same calls.

1. Typed worktree methods (§2.1): move the JSON reading from `hr.py`/`rehome.py` into `orca_cli.py`, same argv. Delete `host_cli` and `Unknown.host_cli`.
2. `supports_agent_hooks` and `manual()` (§2.2, §2.3). Doctor output on Orca does not change.
3. Messages through `missing()` (§2.4). Text change only.
4. `wait_idle` reads the driver's screen predicate (§2.5). tmux's behavior on claude is unchanged; Orca ignores it.
5. A conformance suite: the `FakeHost` behaviors of the test file become one parametrized test run against every backend (real tmux already has its own real-process test). A new host is accepted when it passes it.
6. Rename `orca.py` to `term.py` with a shim (§2.6). Mechanical, last, so earlier diffs stay small.

Steps 1 to 5 are independent of each other except 2 after 1; 6 waits for all.

## 4. What a second host must provide

I found nothing of a `handline.term` layer in this repo, branches or git log, so this assumes only "a host reached over a socket". If it exists elsewhere, check it against this list.

Required (without these, handline cannot start, watch or wake an agent):
- `available`, `own_terminal`, `create_terminal`, `close_terminal`, `list_terminals`, `read_screen`, `send`, `wait_idle`.
- Handles: opaque strings, unique, stable for a terminal's life, and absent from `list_terminals` once it is gone. `knows_handle` must tell its own from another host's (today Orca's from tmux's `%N` by shape; a socket host needs a prefix or a host name saved in the db beside the handle, since a saved handle of another host reads as gone).
- `read_screen` returns the rendered bottom rows, not a byte stream: the dialog readers match on rendered text.
- `send` passes raw bytes and escape sequences (arrow keys, Esc, Ctrl-C answer dialogs). Enter must arrive after the text, not inside it (tmux sleeps 0.4s between them).
- Every call answers within its timeout and never raises; a dead host is `list_terminals() is None`, not an empty set.

Optional, each a flag: `focus`, `ensure_repo` and `create_worktree` (else git does it, as tmux), labels and parents (`supports_labels`), the team card (`supports_runs`), a native idle witness (else the screen predicate).

Not methods, but also assumed, and the real cost of a second machine:
- The agent's process must reach the daemon's unix socket (`~/.orcapipe/daemon.sock`) for its hooks and `hl` commands; a sandboxed agent is granted exactly that (`allow_home_socket`).
- The worktree, the vendor's transcript dir, the account config dir and agent memory are read from the daemon's own disk (collector, `rehome`, `spawn.worktree_path`, trust roots). A host on another machine puts all of those on the other disk.

## 5. Recommendation and decisions for the founder

Do steps 1 to 6 as one task each; none changes what Orca does. A socket host on the **same machine** then needs one new file in `backends/` and one `KNOWN` entry. A host on **another machine** is a separate design, because of the last bullet group above; do not start it from the terminal seam.

Questions:
1. Is the socket host meant to be one machine (cheap, after step 5) or several (needs a filesystem story first)?
2. Does `handline.term` already exist somewhere I should read? Say where.
3. Is deleting `host_cli` acceptable? Anything outside this repo calling it would break.
