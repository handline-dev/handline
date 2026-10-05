# Orca feature inventory (from ~/workspaces/orca-references)

Source: 52 screenshots, each viewed one by one (52/52; the `.mov` is excluded, #13884). Orca is a desktop app (Electron-style) that runs coding agents in git worktrees.

**Evidence column**: `S##` is the screenshot's number in the appendix, with its time stamp. File name = `Screenshot 2026-10-05 at <time>.png`.

**Layer column** (my reading; the founder's definition wins if it differs):
- **term** = the terminal host: terminals, screens, input, worktrees, repos, host CLI verbs, remote hosts.
- **daemon** = the deterministic background loop: agents' state, accounts, usage, schedules, handoff, integrations, search index, sharing.
- **app** = what the person sees and clicks: screens, settings, layouts, notices.
- `a+b` = the feature needs both; the first is the main one.

## 1. Projects, worktrees, sidebar

| # | Feature | Evidence | What it does | Layer |
|---|---|---|---|---|
| F01 | Project list | S01 S08 S42 | Sidebar groups worktrees under projects (git repos and plain folders), each with an icon; scrollable. | app |
| F02 | Add project | S08 S09 | Pick a host, then: browse a folder (repo, folder with many repos), clone from URL, or create an empty project. | term+app |
| F03 | Worktree card | S01 S11 S44 | One card per worktree: name, branch, `primary` badge, `Detached HEAD @sha` badge, status dot (grey, green, yellow bell), terminal and port icons. | app |
| F04 | Parent/child worktrees | S01 S44 S45 | A worktree nests under its parent ("1 child"), so a coordinator's helpers show beneath it. | term+app |
| F05 | Agent rows on a card | S01 S44 S45 | Under each worktree: the agents running there with name, last message snippet, age, working/done icon; "2 agents" collapses a group. | daemon+app |
| F06 | Workspace options | S07 | Group by None/Status/PR/Project; sort; project order; card layout; shown properties; hide sleeping, default-branch, automation-created, CLI-created, detached-HEAD worktrees. | app |
| F07 | Create worktree dialog | S10 | Project, host, "create from" (Smart / GitHub / Branch / Name, base `origin/main`), agent, Advanced, "create more", ⌘↵. | term+app |
| F08 | Create from an issue, PR or URL | S10 S06 S25 | Smart field takes a name, `#1234`, a branch or a GitHub, GitLab or Jira URL; the issue becomes linked context (`{linkedIssue}`) for the agent. | daemon+app |
| F09 | Workspace board | S45 | Kanban of worktrees: Todo, In progress, In review, Done, plus a "Pinned" drop zone; search, filter, drag to change status. | app |
| F10 | Where worktrees live | S18 | Workspace directory, nest by repo name, recognised sources (`.claude/worktrees`, `.gsd-workspaces`, other roots). | term |
| F11 | Branch naming and base | S25 | Branch prefix (git user / custom / none); refresh and fast-forward local main on create; default compare base. | term |
| F12 | Auto-rename branch | S25 | When an agent starts, an auto-named branch is renamed to a short task summary; never after push. | daemon |
| F13 | Confirmations | S18 | Ask before deleting workspaces, automations, artifacts; before closing pinned tabs or running terminals. | app |
| F14 | Open in other apps | S18 | A worktree's "Open" menu launches configured external apps. | app |
| F15 | Worktree actions | S35 | Rename, delete, switch (⌘J), previous/next, select 1–9, create (⌘N). | term+app |

## 2. Terminals, tabs, editor surfaces

| # | Feature | Evidence | What it does | Layer |
|---|---|---|---|---|
| F16 | Tab bar and new-tab menu | S52 S11 | Per-worktree tabs: terminal, browser, markdown, mobile emulator, and agent terminals (Claude, Claude Agent Teams, Codex, OpenCode); search over open tabs, history, files, URLs, agents. | app+term |
| F17 | Terminal behaviour | S27 | Shell choice, GPU renderer, colour contrast, inline images (SIXEL, iTerm, Kitty), scroll speeds, right-click paste, focus-follows-mouse, copy-on-select, trim gutter, OSC 52 clipboard. | term |
| F18 | Workspace setup script | S27 | A repo script runs when a worktree is made, in a background "Setup" tab or a split. | term |
| F19 | Quick commands | S28 | Saved terminal commands, global or per project; run from the tab bar or a right-click. | app+term |
| F20 | Command palette, Go to File, panels | S11 S35 S52 | "Command" button, ⌘P file jump, Explorer, Search, Source Control, Checks panels, left and right sidebar toggles. | app |
| F21 | Keyboard shortcuts | S35 | 130 actions; edit visually or in `keybindings.json`; "Orca first" or "terminal first" per action; modified/unassigned/conflict filters. | app |
| F22 | Input and paste | S33 | Middle-click paste from selection. | term |
| F23 | Tab behaviour | S18 | Tab order (most recent), reusable preview tab for files, pinned tabs. | app |
| F24 | Floating workspace | S31 | A global panel (not tied to a project) with terminal, browser and markdown tabs; start directory; button or status-bar toggle. | app |
| F25 | Appearance | S32 | Theme, language, UI zoom, fonts, terminal themes (30 built in, import from Ghostty, Warp, YAML). | app |

## 3. Browser, emulator, desktop control

| # | Feature | Evidence | What it does | Layer |
|---|---|---|---|---|
| F26 | Built-in browser | S29 S52 | Home page, search engine, zoom, user-agent identity, route links from terminal, markdown and editor into it, profiles. | app |
| F27 | Agent browser use | S29 | Agents drive Orca's browser through the `orca` CLI; cookies imported from Chrome so they are logged in; a skill teaches agents. | term |
| F28 | Mobile emulator | S30 S52 | iOS simulator and Android; agents attach, tap, type via `orca emulator …`; default device. | term+app |
| F29 | Computer Use | S15 | Agents inspect and operate any desktop app, using Accessibility and Screenshots permissions; a skill installs it. | app |
| F30 | macOS permissions | S39 | Microphone, camera, screen recording, accessibility, full disk, automation, local network, USB, Bluetooth, each with request and status. | app |

## 4. Agents

| # | Feature | Evidence | What it does | Layer |
|---|---|---|---|---|
| F31 | Agent registry | S12 S25 | Default agent (Auto, blank terminal, Claude, Agent Teams, Codex, OpenCode), enable/disable, launch command and flags per agent; more named in recipes (Amp, Cursor, Kimi, Copilot, Pi, DeepSeek, Muse…). | daemon+app |
| F32 | Agent status hooks | S12 | Hooks written into each agent's config report working, waiting, done; removable. | daemon |
| F33 | Permission mode | S12 | Yolo (skip permissions) or Manual for every launched agent. | daemon |
| F34 | Trust the folder | S12 | Skip the "trust this folder?" dialog for agents Orca starts, so unattended agents are not stuck. | daemon |
| F35 | Codex server isolation | S12 | Each Codex terminal gets its own server, with a warning when one is shared. | term |
| F36 | Tab titles from the prompt | S12 | Short stable tab names from the first agent prompt; manual rename wins. | daemon |
| F37 | Keep computer awake | S12 S48 | On / Agent (awake while an agent works) / Off; also asks to stay awake with the lid closed. | daemon |
| F38 | Prompt cache timer | S12 | Countdown in the sidebar after a Claude agent goes idle, to resume before the cache expires. | daemon+app |
| F39 | Chat UI (native) | S11 S42 | Structured chat view for Claude and Codex instead of the TUI: model and effort pickers, attach, voice, resume working chats after restart, inherit shell env. | app+daemon |
| F40 | Agent sleep | S42 | Stops idle background agent terminals after an idle window and resumes them on open. | daemon |
| F41 | Agent dashboard, pet, card style | S42 | Experimental: dashboard of agents needing you/working/done, animated pet, new worktree-card layout, terminal-attention highlight. | app |
| F42 | Orchestration | S14 | A skill lets a coordinator hand off a task, hand work to another worktree, run phased or parallel child agents, split a change into PRs; nested depth limit (1 = flat). | daemon |
| F43 | Voice dictation | S16 S35 | ⌘E toggle or hold, microphone and local/cloud speech model choice. | app |

## 5. Accounts and usage

| # | Feature | Evidence | What it does | Layer |
|---|---|---|---|---|
| F44 | Provider accounts | S13 | Several Claude accounts, Codex accounts, Gemini CLI credentials, OpenCode Go key; add, re-authenticate, remove, switch the active one without losing sessions. | daemon |
| F45 | Usage meters | S01 S46 S47 | Status bar and popup: Claude 5-hour and weekly use with time to reset, Codex 5-hour; per-account detail; detailed or compact; manage accounts. | daemon+app |
| F46 | Stats and token ledger | S36 | Agents spawned, time agents worked, PRs created; opt-in scan of Claude, Codex, OpenCode, Muse logs into a token ledger. | daemon |
| F47 | Agent session search | S24 | Per-computer index of every agent conversation and tool output; open from the sidebar or ask an agent to find a session. | daemon |

## 6. Tasks, automations, source control

| # | Feature | Evidence | What it does | Layer |
|---|---|---|---|---|
| F48 | Tasks view | S06 S04 | GitHub Issues / PRs / Projects, Open / Assigned to me, query syntax, columns (assignee, status, updated), pagination, new issue, **Start →** makes a worktree with the issue linked. | app+daemon |
| F49 | Task sources | S26 S04 S05 | GitHub, GitLab, Linear, Jira: connect, show or hide in Tasks; Linear also gets an agent skill; Jira Cloud or self-hosted with an API token stored encrypted locally. | daemon |
| F50 | Integrations | S19 | Review providers GitHub (`gh`), GitLab (`glab`), Bitbucket, Azure DevOps, Gitea; task provider Linear; account scope per host. | daemon |
| F51 | Automations list | S01 S21 | Search, filters, templates (weekday repo audit, release readiness, daily change review, hourly queue check), New Automation. | app+daemon |
| F52 | Automation definition | S02 | Prompt (skills, paths, `/goal`), agent, host, project, workspace (existing worktree or new per run), session fresh or reuse, schedule, grace window, precheck command with timeout; Orca or Hermes runtime. | daemon |
| F53 | Automation runs | S03 | Successful/failed counts for 24 h and 7 d; table of runs: trigger, host, status. | daemon+app |
| F54 | Source control AI actions | S25 S35 | Commit message and PR title/description generated by a chosen agent from a template (`{branch}`, `{stagedFiles}`, `{stagedPatch}`, `{linkedIssue}`); change group order. | daemon+app |

## 7. Remote, mobile, sharing

| # | Feature | Evidence | What it does | Layer |
|---|---|---|---|---|
| F55 | SSH hosts | S37 S51 S09 S10 S02 | Saved targets with test/connect; files, terminals, git and worktrees run on them; the host is a choice in project, worktree and automation dialogs. | term |
| F56 | Remote Orca servers | S38 | Connect to another Orca runtime, share this machine, or manage cloud VMs, for persistent sessions and web/mobile handoff. | term+daemon |
| F57 | Orca Mobile | S20 | Pair a phone by QR over Orca Relay (any network) or LAN/Tailscale; paired-device list with revoke; terminal resized for the phone, restore on leaving. | daemon+term |
| F58 | Orca account | S17 | Sign-in that unlocks artifact sharing, Orca Relay and skill sharing. | daemon |
| F59 | Artifacts | S22 | Publish an HTML or Markdown file as a public link, copy, preview, delete; off by default. | daemon+app |
| F60 | Share skills | S23 | Bundle skills behind an unlisted link, install from a link, revoke; agents and the CLI may publish only if allowed. | daemon |

## 8. Status bar, system, housekeeping

| # | Feature | Evidence | What it does | Layer |
|---|---|---|---|---|
| F61 | Status bar | S01 S48 S49 S50 S51 | Usage meters, keep-awake, memory, terminal count, port count, remote-host count; each opens a popup. | app |
| F62 | Resource manager | S49 | CPU and memory per project, worktree and terminal with graphs; "clean up workspaces"; disk "Space" scan (beta). | daemon+app |
| F63 | Ports | S50 | Listening ports per workspace and external ones, with the process behind each. | daemon+app |
| F64 | Notifications | S34 | Desktop alert when an agent finishes or a terminal rings; sound; suppress when the worktree is visible; test button. | app+daemon |
| F65 | Plugins | S43 | Experimental plugin system, off by default; install and enable individually. | daemon |
| F66 | Privacy, diagnostics, network | S40 S41 | Anonymous usage toggle; redacted diagnostics file; HTTP/1.1 mode; proxy. | app |
| F67 | Help menu | S44 | Shortcuts, feedback, onboarding, docs, changelog, links, update check, restart. | app |
| F68 | Settings shell | S12 S35 | Searchable settings (⌘F), grouped: AI capabilities, Set up, Workflows, Interface, Remote hosts, Privacy, Advanced, Experimental, per-project. | app |
| F69 | Window chrome | S01 | Back/forward history, sidebar toggle, Search, Tasks, Automations entries. | app |

## 9. Layer totals

By main layer, 69 rows: daemon 28, app 27, term 14. Pure rows: app 21, daemon 17, term 8; the other 23 span two layers.

Reading: the mass of Orca's value sits in daemon-type work (agents' state, accounts, usage, schedules, integrations, search, sharing), not in the terminal itself. The term layer needs few verbs (create, read, send, wait, worktree, repo, host) and the app layer is mostly lists, settings and popups over what daemon and term already know.

## Appendix: the 52 screenshots

| S | Time | What the screen shows | Features |
|---|---|---|---|
| S01 | 22.23.43 | Automations list with templates; sidebar; status bar | F01 F03 F05 F45 F51 F61 F69 |
| S02 | 22.23.54 | Create automation dialog | F52 F55 |
| S03 | 22.24.00 | Automation runs | F53 |
| S04 | 22.24.07 | Tasks, Jira not connected | F48 F49 |
| S05 | 22.24.09 | Connect Jira site | F49 |
| S06 | 22.24.15 | GitHub issues table | F08 F48 |
| S07 | 22.24.24 | Workspace options menu | F06 |
| S08 | 22.24.27 | "Add project" tooltip on sidebar icons | F01 F02 |
| S09 | 22.24.29 | Add a project dialog | F02 F55 |
| S10 | 22.24.32 | Create worktree dialog | F07 F08 F55 |
| S11 | 22.24.39 | Claude Chat tab | F03 F16 F20 F39 |
| S12 | 22.24.51 | Settings, Agents | F31 F32 F33 F34 F35 F36 F37 F38 F68 |
| S13 | 22.24.53 | Settings, AI Provider Accounts | F44 |
| S14 | 22.24.55 | Settings, Orchestration | F42 |
| S15 | 22.24.56 | Settings, Computer Use | F29 |
| S16 | 22.24.58 | Settings, Voice | F43 |
| S17 | 22.25.00 | Settings, Orca Account | F58 |
| S18 | 22.25.02 | Settings, General | F10 F13 F14 F23 |
| S19 | 22.25.04 | Settings, Integrations | F50 |
| S20 | 22.25.06 | Settings, Mobile | F57 |
| S21 | 22.25.09 | Settings, Automations | F51 |
| S22 | 22.27.03 | Settings, Artifacts | F59 |
| S23 | 22.27.07 | Settings, Share Skills | F60 |
| S24 | 22.27.08 | Settings, Agent Session Search | F47 |
| S25 | 22.27.12 | Settings, Git & Source Control | F08 F11 F12 F31 F54 |
| S26 | 22.27.15 | Settings, Task Sources | F49 |
| S27 | 22.27.17 | Settings, Terminal | F17 F18 |
| S28 | 22.27.19 | Settings, Quick Commands | F19 |
| S29 | 22.27.21 | Settings, Browser | F26 F27 |
| S30 | 22.27.22 | Settings, Mobile Emulator | F28 |
| S31 | 22.27.24 | Settings, Floating Workspace | F24 |
| S32 | 22.27.26 | Settings, Appearance | F25 |
| S33 | 22.27.27 | Settings, Input & Editing | F22 |
| S34 | 22.27.29 | Settings, Notifications | F64 |
| S35 | 22.27.32 | Settings, Shortcuts | F15 F20 F21 F43 F54 F68 |
| S36 | 22.27.33 | Settings, Stats & Usage | F46 |
| S37 | 22.27.35 | Settings, SSH Hosts | F55 |
| S38 | 22.27.37 | Settings, Remote Orca Servers | F56 |
| S39 | 22.27.38 | Settings, macOS Permissions | F29 F30 |
| S40 | 22.27.40 | Settings, Privacy & Telemetry | F66 |
| S41 | 22.27.41 | Settings, Advanced | F66 |
| S42 | 22.27.43 | Settings, Experimental | F01 F39 F40 F41 |
| S43 | 22.27.45 | Settings, Plugins | F65 |
| S44 | 22.27.50 | Help menu; sidebar with agent rows | F03 F04 F05 F67 |
| S45 | 22.27.53 | Workspace board | F04 F05 F09 |
| S46 | 22.27.57 | Usage popup | F45 |
| S47 | 22.27.58 | Usage detail for Claude | F45 |
| S48 | 22.28.08 | Keep-awake popup | F37 F61 |
| S49 | 22.28.10 | Resource manager popup | F61 F62 |
| S50 | 22.28.12 | Ports popup | F61 F63 |
| S51 | 22.28.14 | Remote hosts popup | F55 F61 |
| S52 | 22.28.22 | New-tab menu | F16 F26 F28 |

Full file names: `Screenshot 2026-10-05 at <Time>.png`, e.g. S01 = `Screenshot 2026-10-05 at 22.23.43.png`.
