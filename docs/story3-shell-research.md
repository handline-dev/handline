# Story 3-0: plain terminals next to agents

Research only. Sources are web search results of 2026-10-14; second-hand where marked, so check a licence file before copying any code.

## 1. How similar tools handle plain shells

| Tool | Tabs and splits | Folder | Persistence | Input | Licence | Borrow |
|---|---|---|---|---|---|---|
| Orca | A tab holds one thing (terminal, editor, browser, diff); terminal tabs split inside the tab; drag to split ([docs](https://www.onorca.dev/docs/model/tabs-panes-splits)) | per worktree | scrollback survives restart; live processes not confirmed | the local user | MIT (second-hand) | tab = one surface; a split inside a terminal tab; saved quick commands |
| Conductor | Run, setup and test scripts plus a terminal per workspace ([docs](https://docs.conductor.build/core/scripts)) | the workspace's worktree; `CONDUCTOR_PORT` per workspace | not documented | the local user | closed source | a Run button for a repo script, with a port per workspace |
| Vibe Kanban | one terminal and dev server per attempt | the attempt's worktree | worktrees are cleaned up when orphaned or expired | the local user | Apache-2.0 (second-hand) | start in the task's worktree; clean up with it |
| Claude Squad | tmux session per agent, a TUI to attach ([README](https://cdn.jsdelivr.net/gh/smtg-ai/claude-squad@main/README.md)) | git worktree per session | tmux keeps it alive | whoever attaches | AGPL-3.0 | tmux as the host (we already do). Do not copy code: AGPL |
| Cursor | integrated terminal tabs; agent shells appear as tabs too | the project folder | processes stop on restart or update; stale agent tabs pile up; staff advise tmux ([forum](https://forum.cursor.com/t/preserve-terminal-processes-across-updates-restarts/147353)) | the local user | closed source | the failure to avoid: tmux-backed shells that outlive the app, and a way to close stale ones |

None of them has more than one person watching one shell, or hands the keys between people. That is ours already (§2).

## 2. What our daemon and web pane already do that a shell can reuse

- **Host.** Terminals are tmux panes; `terminal.attach` takes `agent` or a bare `terminal` handle, "a handle no agent has is watched while the host lists it". A shell pane can be watched today; it has no input holder, so `terminal.input` and `terminal.resize` refuse it. That is the gap.
- **Bytes channel.** snapshot on attach, `bytes` notices, `terminal.history`, `terminal.detach` ("never stops the agent"): the same for a shell.
- **Input holder.** `input.take` and `release`, one holder, `terminal.input` refused for anyone else; "a pane showing a shell prompt takes it". Reuse as is once a shell has a holder.
- **Resize** (#73, #89): the holder's size wins; with no holder any attached viewer sizes it, floor 80 by 24, and the first size is restored on the last detach. Right for a shell.
- **Web pane.** `ByteTerminalView` and the xterm adapter take a handle; holder strip, copy and paste (#64) and follow-the-window (#74) carry over. The phone has no xterm and uses the text-grid path.
- **Missing for a shell:** a way to create one (`terminal.new {cwd}`), a list of shells (today `debug.terminals` only), a close that is not `terminal.close {agent}`, a name, and a rule for what happens to a shell on daemon restart (tmux keeps it, so the daemon must re-adopt it).

## 3. Proposal

- **3-1 (daemon):** `terminal.new {cwd?, name?}`, a `shells` list with a topic, `terminal.close {terminal}`, an input holder per shell, re-adopt tmux shells on start. Contract change first, fake daemon second.
- **3-2 (PC):** a "New shell" in the tab menu, the shell in the same tab strip as agent terminals, working folder picked from the agent's worktree or the repo, shells survive an app restart, Close asks first when a process runs.

Open for the founder: shells in the worktree of a task or only in a repo folder; whether a shell may be shared by two people (the holder rule allows it); phone shows shells read-only (text grid) in v1.
