# First-run walkthrough

Handline walked as a first-time user, from nothing to a merged change, through the PC app. Where the app could not do a step, the CLI was used and the section says so.

## How it was run

- Build: `pnpm install`, `tools/build-go.sh`, `pnpm --filter @handline/web build`, then the app in Own-daemon mode (`HANDLINE_MODE=own`), as `apps/electron/README.md` describes. Main at `67f4d5063`, macOS, Claude Code 2.1.300.
- Scratch home `/tmp/hl-fr3`, its own app data folder, gateway on `127.0.0.1:7851`, its own tmux socket. The live daemon and the real team were never used.
- One live agent was needed. As `tools/live_account.py` picks, the account with the most headroom was borrowed through `HANDLINE_TEST_ROOT` and `HANDLINE_LIVE_ACCOUNTS` (the way `tools/live_e2e.py` does), because a real login needs a person at a browser. The agent was a haiku agent in permission mode `default` on a two-line scratch git repo.
- The window was driven with the DevTools port (`HANDLINE_DEBUG_PORT`). Clicks went through the page's own buttons, so mouse hit-testing was not exercised.
- Not walked: a real account login, the phone, and a terminal agent past its login failure (step 6).

**Harness workarounds.** These are not product findings. They kept the walk going: the test root (founder key stored under it, so the app's key path was bridged by a symlink), a wrapper binary that adds `-claude-version 2.1.300` (finding P12 is the product side of this), and `structured: true` set by hand in `agent.yaml`. The first run without a tmux socket of its own put the agent's terminal in the founder's live tmux session. That window was closed at once, and nothing else of the live team was touched.

## 1. Install and first launch

**Did.** Read `README.md`, then built from source and started the app on an empty home.
**Expected.** An installer or a download, then a window.
**Happened.** The README says "you cannot install it and use it yet". `docs/setup.md` describes a launchd daemon, a Python `hl` and Orca; the app path is in `docs/run-on-real-daemon.md`, aimed at the founder. The app showed a tray icon and no window for about 25 s, then a window.
**Stuck.** With `HANDLINE_HOME` and `OCP_HOME` both set and different, the daemon exits at once (`name different homes`); the only sign is a line in the app's `daemon.log`, and the window just never appears. (A developer-environment case, but the same silence would hide any daemon start failure.)
**Needed the CLI.** Yes: `pnpm`, a Go build, a shell.
**Screenshot.** `img/first-run/01-first-launch.png`: the app on a fresh home pairs itself without any CLI. The sidebar says "No agents." and the main area says "Pick an agent". Nothing says what to do first.

The inbox already holds one item (`img/first-run/01b-inbox.png`), a "Disk space is low" notice. It is true of this Mac, but it is the first thing a new user sees, and nothing welcomes them.

## 2. Connect an account

**Did.** Opened Accounts, then used `hl init` and `hl account add`.
**Expected.** An "Add account" button that leads through the Claude login.
**Happened.** Accounts says only "No accounts configured." (`img/first-run/02-accounts.png`): no button, no hint. `hl doctor` says `hl account add <name>`. `hl account add alpha --no-login` registered it; the Accounts page still said "No accounts configured." (`img/first-run/02-account-registered.png`) while `hl account list` showed it. The login itself (`claude /login` in a terminal, with a browser) was not walked.
**Stuck.** Registered but not logged in is invisible in the app. The Python `hl login` listed the borrowed account as "NOT logged in" because it checks the scratch path, while the daemon found the borrowed login (harness effect).
**Needed the CLI.** Yes, fully. `hl init` is also needed before anything: without it `hl doctor` reports `shared/.claude/settings.json missing` and the hooks missing, and the app never runs it.

## 3. Add a project

**Did.** Looked for "add project", "clone" or "open folder" in the app, then `hl agent register worker --model haiku --repo /tmp/hl-fr3-proj`.
**Expected.** A button for a folder or a git URL.
**Happened.** None exists; the Worktrees page says "A worktree appears when an agent is started in a repo." A project exists only as the `repo:` of an agent. Registering printed `launch it from tmux with: tmux: a terminal in <selector> running "hl launch worker"`, which does not match the app's Start button. The agent appeared in the sidebar at once.
**Needed the CLI.** Yes. There is no clone at all.

## 4. Folder trust

**Did.** Watched for the Handline roots and the Claude Code trust dialog; `hl trust list`.
**Expected.** A prompt to trust the folder, from Handline or from Claude Code.
**Happened.** Neither appeared. `hl trust list` stayed `no declared roots` through the whole run, and the structured agent ran in the worktree (`/tmp/hl-fr3-proj.worktrees/worker`, made by the first Start) without any dialog. The terminal agent never got as far as a trust dialog: it stopped at the login message (step 6).
**Verdict.** Not observed, so the step is untested rather than fine. The borrowed account's config dir may already trust the paths, and a structured session may skip the dialog by design. A real new user on a terminal agent should be walked through this once with a fresh account.
**Needed the CLI.** `hl trust` is CLI only; the app has no screen for roots.

## 5. Build a team

**Did.** Team page; `hl team init`; edited `team.yaml`.
**Expected.** A team made of the agents just registered.
**Happened.** The Team page says "No agents in the team" with no button (`img/first-run/05-team-empty.png`); HR says "Nothing to decide". `hl team init` writes a starter `team.yaml` naming `backend`, `reviewer` and `archivist`, none of which exist, and not the agent just registered. After registering, the Team card shows "(describe the role)" and "haiku · medium" (`img/first-run/05-team-registered.png`), but the agent is not in the routing table. `hl doctor` warns `team.yaml names agents ocp does not have`. Roles, links and the account choice per agent: no app screen; roles by file, links by `team.yaml` edges, accounts by the daemon.
**Stuck.** Editing a file by hand makes the daemon refuse it (`changed outside the founder's path and is not trusted ... run hl team seal`). The app's Start button showed only `start: internal error` for that, and the cause is in `daemon.log`.
**Needed the CLI.** Yes: `hl team init`, a text editor, `hl team seal`, and `hl agent set` (which cannot set `structured`; the harness edited the file).

## 6. Give the first work

**Did.** Opened the agent, pressed Start, then typed a task in the chat box.
**Expected.** Start runs the agent; a message gives it work.
**Happened, Start.** The first Start made a terminal agent, which printed `no account is logged in. A human has to do this once` in a terminal the app does not show. The app showed only a banner: `... started in <worktree> (terminal %0) but no session is running in it after 20s - the terminal is there and claude is not. Go and look (t)`. Start was then refused because the installed Claude (`2.1.300`) is newer than the daemon accepts (`2.1.296`). After the harness fixes (structured agent, `-claude-version`), Start worked and the agent began on its own wake prompt with no task: it went looking for a "journal" and "inbox" in the new project and ended its turn asking the founder where they are.
**Happened, the message.** Sending the task from the chat box failed with `send: unknown target 'worker'`, because the agent was not in `team.yaml` (step 5). The message showed twice in the chat. After fixing `team.yaml` it went through.
**Stuck.** The route from "agent exists" to "agent has work" has two silent failures. The Tasks page has a `+` button (`img/first-run/08-tasks.png`), but chat was used here, and the agent then opened task T1 itself.
**Needed the CLI.** Yes, for every fix above.

## 7. Watch it run, and needs-you

**Did.** Watched the agent's row, the chat, the inbox and the status bar; allowed or denied each permission.
**Expected.** A running state, and a clear card when it needs me.
**Happened.** Good: the row, the inbox count (2), the status bar bell and a permission card with the command and Allow/Deny with key hints all show at once (`img/first-run/07-permission-asked.png`). Problems: the agent's own channel to the founder asks permission too (`hl say human ...`, `hl task begin`, `hl task done`; `img/first-run/07-permission-hl-say.png`), so one tiny task took about 11 approvals (the `ls`, 2 `hl say`, 5 `hl task`, the file write, 2 for `git add` and commit). At 1280 px the sidebar row loses the agent name once the "permission" pill shows, and the tab strip shows two truncated "C.. ! no terminal" tabs. The first permission asked (an `ls` of the account's own memory folder) was something no new user could judge.
**Needed the CLI.** No.

## 8. Review the result

**Did.** Inbox, Tasks > Closed > T1, Worktrees.
**Expected.** A "ready for review" item with the diff or commit.
**Happened.** The inbox got no "work finished" item, only a daemon notice that a terminal had been closed (`img/first-run/08-inbox-after.png`). The agent closed its own task. The task page (`img/first-run/08-task-detail.png`) shows the brief, history and evidence text ("hello.txt committed on worker branch"), with no diff, no commit link and no accept or reject step. Worktrees (`img/first-run/09-worktrees.png`) shows the branch as `clean`, `0` files, with no sign that it is one commit ahead.
**Needed the CLI.** Plain `git` in the worktree is the only way to see the change.

## 9. Merge

**Did.** Looked for a merge or land action in the app and the CLI.
**Expected.** A button or one command that puts the branch on `main`.
**Happened.** None. The Worktrees row does nothing when clicked. `hl task merge` folds tasks into one another, not branches, and the daemon's `-land-repo` flag is now ignored. The branch was merged with plain `git merge --ff-only worker`.
**Needed the CLI.** Yes, and not even Handline's: `git`.

## 10. Come back the next day

**Did.** Quit and reopened the app (the daemon and the agent's session kept running), then looked at what had changed.
**Expected.** A summary of what happened while away.
**Happened.** Everything persisted: the agent, its chat history, T1, the inbox (2), and Stats (`1 tasks done`, `worker 5 m`). The window opens on the empty "Pick an agent" screen (`img/first-run/10-next-day.png`), not on what needs attention. The disk-space notice was still the top inbox item.
**Needed the CLI.** No.

## Problems

Severity: **blocker** stops a new user, **major** costs a new user a long time or a wrong turn, **minor** is rough but passable.

| # | Step | Problem | Severity |
|---|---|---|---|
| P1 | 1 | No install path: the README says it cannot be installed; the setup doc describes an older stack (launchd, Python `hl`, Orca) | blocker |
| P2 | 1 | A daemon that fails to start leaves no window and no message; the cause is only in `daemon.log` (also about 25 s of nothing on a healthy start) | major |
| P3 | 1 | Empty state ("No agents." / "Pick an agent") says nothing about what to do first; the first inbox item is an unrelated disk warning | major |
| P4 | 2 | Accounts has no way to add or log in an account; a registered account that is not logged in does not show at all | blocker |
| P5 | 2 | `hl init` is needed first and the app never runs it | major |
| P6 | 3 | No add or clone project in the app; a project exists only as an agent's `repo:`; `hl agent register` ends with a message about tmux that does not match the app | blocker |
| P7 | 4 | Folder trust not observed (`hl trust list` empty throughout); no app screen for trust roots; the terminal-agent trust dialog was not reached | major (untested) |
| P8 | 5 | The starter `team.yaml` names agents that do not exist and omits the registered one; the Team page has no add or edit | blocker |
| P9 | 5 | A hand edit is refused as untrusted; the app says only `start: internal error` | major |
| P10 | 6 | Chat to a registered agent fails with `unknown target`; the message is also shown twice | major |
| P11 | 6 | An unlogged-in terminal agent fails inside a hidden terminal; the app banner says "go and look" | major |
| P12 | 6 | The app-started daemon refuses a newer Claude (`2.1.300` vs `2.1.296`); the app passes no `-claude-version`, and only a banner says so | major |
| P13 | 6 | Start runs an agent on a wake prompt with no task; it hunts for a "journal" and "inbox" and asks the founder where they are | minor |
| P14 | 7 | The agent's own `hl say` / `hl task` calls ask permission: about 11 approvals for a one-line change | major |
| P15 | 7 | Sidebar row hides the agent name when the permission pill shows at 1280 px; the tab strip shows duplicate truncated tabs | minor |
| P16 | 8 | No "ready for review" item, no diff or commit on the task, no accept or reject; the agent closes its own task | blocker |
| P17 | 8 | Worktrees shows `clean`, `0` files for a branch one commit ahead | major |
| P18 | 9 | No way to merge a branch into `main` from the app or from `hl` | blocker |
| P19 | 10 | The app opens on an empty "Pick an agent" screen, not on what needs attention since last time | minor |
| P20 | all | The scratch home shares the default tmux server unless told otherwise: a terminal agent landed in the founder's live tmux session | major |
