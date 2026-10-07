# Handline web UI: screens and information architecture

Approved by the founder. Founder's brief: base it on the Orca screenshots, fit it to our system, and let a person **control Claude Code directly**. Scope here is screen design and information architecture only. The API is drafted separately from the orcapipe team; §8 is this document's input to it.

Delivery order: **web first**, Electron wraps the same web app, mobile is design only (§7).
Orca source: `docs/orca-feature-inventory.md` at 67fd404f (F01..F69, screenshots S01..S52). IDs below point at it.

## 1. What makes ours different from Orca

Orca is a place to run agents by hand. Handline is a place where a daemon runs a team and a person steps in. Three things follow, and the layout is built on them.

1. **The unit is an agent, not a worktree.** An agent has a role, an account, a session, a terminal and a worktree. Orca's sidebar is worktree-first with agents under it (F03, F05); ours is agent-first with the worktree as an attribute.
2. **The daemon is also typing at the agents.** It delivers tasks, wakes, hands off, closes idle sessions and presses Esc at dialogs. A person typing into the same terminal races it. Every control surface therefore shows *who may type now* (§4.3).
3. **Accounts and usage decide what runs.** In Orca they are a settings page and a status-bar meter (F44, F45). Here they are a first-class screen, because a limit moves agents.

## 2. Frame (every screen sits in it)

```
┌ top bar ───────────────────────────────────────────────────────────────┐
│ ◀ ▶  [⌕ search ⌘K]            Inbox ③   Tasks   Team   Accounts   ⚙     │
├ rail ──┬ main ─────────────────────────────────────────────┬ side ─────┤
│ agents │  tabs: [handline.term-design ▸ Agent] [Task 14020] [+]     │ context  │
│ (§3)   │                                                              │ panel    │
│        │                    (the screen)                              │ (§4.5)   │
├ status bar ────────────────────────────────────────────────────────────┤
│ daemon ● · acct alpha 41% · beta 12% · 7 agents · 3 running · host ●    │
└────────────────────────────────────────────────────────────────────────┘
```

- **Top bar**: history back/forward and search (F69, F20), and the four destinations a person returns to all day: Inbox, Tasks, Team, Accounts. Settings behind the gear.
- **Left rail**: the agent list (§3.1). Collapsible; on narrow widths it becomes a drawer.
- **Main**: tabs per opened thing (an agent page, a task). Tabs persist per browser.
- **Right panel**: context for the selected agent (§4.5). Collapsible.
- **Status bar**: daemon alive, per-account usage with reset time (F45, F61), counts, host state. Each item opens a popup like Orca's (S46..S51).
- **⌘K command palette** (F20): jump to any agent/task/screen, and run verbs (start, stop, send to, new task). Shortcuts are fixed in v1 (no editor, see F21 in §6).

## 3. Screen list

| # | Screen | Purpose | Orca ancestor |
|---|---|---|---|
| A | Agent rail | find an agent, see its state at a glance | F01 F03 F05 |
| B | **Agent page** | chat with the agent, plus a movable pane holding its Claude Code terminal or a shell | F16 F39, S11 |
| C | Inbox | what waits on the founder: results, decisions, permission asks, blocks | none (our `hl inbox`, bridge) |
| D | Tasks | requests between agents and people, with their state | F48 (loosely), S06 |
| E | Task detail | one request whole, with its answer and evidence | none |
| F | Team | who may talk to whom, roles, proposals (`hl hr`) | none |
| G | Accounts and usage | accounts, windows, caps, who is on which | F44 F45, S13 S46 S47 |
| H | Worktrees | repos, worktrees, parent/child, diff and status | F01 F04 F09, S45 |
| I | New agent / new worktree | register and start | F07, S10 |
| J | Schedules | cron messages typed at agents | F51 F52, S21 S02 |
| K | Stats | what the team spent and finished | F46, S36 |
| L | Settings | terminal host, notifications, appearance, trust folders, daemon | F68 and S12..S43 |
| M | Notifications and popups | status-bar popups, toasts | F64, S46..S51 |

### 3.1 A. Agent rail

One row per agent, grouped by team role (not by project; a project filter is a chip). Row:

```
● handline.term-design     opus   alpha
  working · fix flaky test · 3m           ▮▮▮▯ 41%
```

- State dot: running (green), idle (grey), waiting on you (amber, bell), stalled/blocked (red), parked/queued (hollow), no terminal (dashed). These are the daemon's states plus the *block* kinds (`no terminal`, `no session`, `stalled`, `host down`).
- Line 2: state text, current task id, time in state. Right: account and its window use.
- A permission prompt or dialog on the agent turns the row amber and sorts it up. This is the Orca "bell" (F03) made specific.
- Parent/child nesting (F04) is kept only where `hl spawn` made a child: the child nests under its spawner.
- Rail options (kept, simplified from F06): group by role / state / account, hide parked, search. No card layout choices.
- Row menu: Open session, Start, Stop, Resume, Rename, Move account, Close.

### 3.2 B. Agent page (the screen that matters most)

The page is a **chat** like the handline bridge's chat page, with a **pane area** beside it. The chat is the default way to work with an agent; the terminal is one step away when a person wants the real Claude Code TUI.

```
┌ header ────────────────────────────────────────────────────────────────┐
│ ● handline.term-design   opus · alpha 41% · fix flaky test · worktree term-design│
│ [▶ Start] [■ Stop ▾] [⟳ Resume]                     input: daemon ▾     │
├ chat ──────────────────────────────┬ pane area ─────────────────────────┤
│ you      fix the flaky test        │ [Claude Code ✕] [shell ✕] [＋]     │
│ agent    delivered, not answered   │ ┌────────────────────────────────┐ │
│ ─ task done: evidence … ─         │ │ live text grid of the TUI     │ │
│ ⚠ Permission: Bash `git push …`    │ │                                │ │
│   [Yes y] [No n]                   │ └────────────────────────────────┘ │
│ ▸ type a message…        [Send ↵]  │ keys: [Esc] [^C] [↵] [↑] [↓] [1][2][3]│
└────────────────────────────────────┴────────────────────────────────────┘
```

**Chat (left).** The conversation between the person and the agent, as the bridge draws it: messages with their landing state (sending, delivered, delivered not answered), task events inline (opened, done with evidence, withdrawn), a working/waiting line, and older messages loaded when scrolled to the top. The agent's reply and tool activity come from the transcript (§8, chat data). Two tabs as on the bridge: *mine* (person and this agent) and *all* (everything this agent sent or received). A permission or question prompt is drawn as a card in the stream with its buttons; it is also the Inbox card (§3.3).

**Pane area (right).** Holds panes of kinds: **Claude Code** (this agent's own terminal, live), **Shell** (a plain terminal in its worktree), and a reserved **Review** (§3.7). Like tmux or Orca:

- Open from `＋` or the header: Claude Code, Shell.
- Dock to the right (default), left, bottom, or fill the page; drag a pane's tab to another edge to move it.
- Several panes can sit side by side as splits, or be **merged into one tabbed group** by dropping one onto another's tab strip; drag out to split again.
- The layout is remembered per agent in the browser; closing a pane never stops the agent (closing the Claude Code pane detaches only).
- On narrow screens the pane area becomes a second tab beside Chat (§7).

Controls (header and Claude Code pane):

| Control | Does | Notes |
|---|---|---|
| **Start** | create a terminal in the agent's worktree and launch `hl launch <agent>`; or, if a closed session exists, resume it | shows the account the daemon would pick (`account_pick` on `agent.get`) and lets the person override it once |
| **Stop ▾** | *Interrupt* (Esc, ends the turn), *Exit* (`/exit`), *Close* (ends the session, keeps terminal, worktree, account; frees memory), *Kill* (kills the terminal and session; confirm); then, once no session runs, *Close terminal* (the host refuses it while a session runs, so the UI stops first) | the same words as the API's `session.interrupt` and `session.stop` exit/close/kill; "close" always means end the session and keep the terminal |
| **Resume** | wake the same session | enabled when the session is closed and exists |
| **Chat box** | sends a message to the agent as a request, the way the bridge does | goes through the daemon's delivery (queued, typed, landed), not raw keystrokes; Shift+Enter is a newline |
| **Key chips** | send one key to the terminal: Esc, Ctrl-C, Enter, arrows, Tab, Shift+Tab (mode cycle), 1/2/3 (menu pick) | the answer to dialogs, which are menus; primary input on mobile (§7). Sent with `session.keys`, after an explicit take (§4.3) |
| **Permission card** | Yes / No on a detected Claude permission prompt | appears only when the daemon *detected* one (tool, command, reason, the prompt's own lines); never offers "always". Same card in Inbox |
| **Other dialogs** | trust-folder, `/exit` over background work, login expired, unknown dialog | card with the dialog's lines and key chips; the daemon names what it cannot decide and never picks for the person |
| **Terminal pane** | the live stream as a **text grid** in v1 (rows of text; no colour or cursor addressing yet), selectable, copyable, scrollback | printable text typed in the pane is sent with `session.input` and named keys with `session.keys`, once the person *takes the keyboard* (§4.3); while a dialog is open text goes through `prompt.answer` with a free-text choice, and keys through `session.keys` |

Chat box and terminal are two ways to speak: the chat box for a message to the agent (queued, tracked, safe while the agent works), the terminal for a person who needs the TUI itself (menus, `/` commands, a dialog).

### 3.3 C. Inbox

A single list of what waits on the person, newest first, each with its action inline:

- **Result** (a task done): title, evidence, `Read whole`, `Mark read`, `Reply / New task`.
- **Decision** (a task owned by the founder): the question and its options; answer inline.
- **Permission ask / dialog** from an agent: the same banner as §3.2, answerable without opening the session.
- **Block** (a delivery waiting behind an obstacle): which agent, what is in the way, since when.
- **HR proposal** (roster/ROLE change): diff and Approve / Reject.

Filters: kind, agent. Bell count feeds the top bar and the browser tab title.

### 3.4 D, E. Tasks and task detail

Table (agent, from→to, title, state queued/delivered/running/done/withdrawn/cancelled, age, time box). Row opens detail: body, relation (supervisor/peer), response mode, the delivery history, evidence, events folded into it, links to the sessions involved. Actions: New task (to an agent, with time box `-t`), Withdraw, Cancel, Edit while queued. Replaces Orca's Tasks view (F48); issue-tracker sources are dropped (§6).

### 3.5 F. Team

Graph or table of agents with the allowed contact edges from `team.yaml`, each agent's role (ROLE.md) and base.md size, and pending HR proposals. Read-mostly in v1: editing roster and ROLE stays the founder's flow through `hl hr`, shown here as proposals to approve (the web UI does not write ROLE.md or base.md directly).

### 3.6 G. Accounts and usage

Per account: login state, 5-hour window used and reset time, weekly use, cap on agents mid-turn, which agents sit on it, source marks (`*` unused, `~` earlier reading, `!` stale: the same marks as the TUI). Actions: Add (opens the login in a terminal tab that the person types into; the web UI never touches credentials), Re-login, Set cap. Also shows recent moves ("beta → gamma at 10:12, limit"). Orca S13, S46, S47.

### 3.7 H. Worktrees

Table/board of worktrees: repo, branch, owner agent, parent/child, status (todo/in progress/in review/done, as the daemon already sets with `worktree set`), changed files count. Board view kept from Orca (F09, S45) because the status field already exists on the host. Opening a worktree shows its branch, changed-file count and an "Open agent" link. **Diff review is wanted but later**: the place is reserved (a `Review` pane kind in the pane area, and a Changes tab here) and not designed now. v1 builds nothing there; the API only needs to leave room (§8). No editor.

### 3.8 I. New agent / new worktree

One dialog: repo (pick or add), name, role, model, effort, account (auto or pick), base ref, first task (optional). Result: registered agent plus worktree plus started session. Orca S10 reduced; "create from issue/PR/URL" dropped.

### 3.9 J. Schedules

List of cron messages typed at agents (target, cron, message, next run, last run). Add, edit, remove are founder-only actions in the daemon; the screen shows them disabled for other roles. Orca S21, S02 reduced to what `hl schedule` does.

### 3.10 K. Stats

Tasks done, agent time, account use over 24 h/7 d, moves and handoffs, blocks raised. Orca S36 without the token-ledger log scan.

### 3.11 L. Settings

Terminal host (Orca / tmux / socket) and its health; daemon (idle timeout, idle-background, notice digest, cache-expiry); notifications; appearance (theme, zoom, terminal font and theme); trust folders; connected devices (§7); privacy. Searchable, grouped left list like S12.

### 3.12 M. Popups and toasts

Status-bar popups: usage (G summary), daemon, host/terminals, resources. Toasts: task done, permission ask, stall, account out. Browser notifications (and Electron's native) per F64, suppressed when that session is on screen.

## 4. Controlling Claude Code: the interaction model

### 4.1 One session, two readings

For each agent the daemon holds both the **raw terminal stream** (for the pane) and **parsed facts** (turn running/idle, dialog kind and lines, permission prompt, current task). The pane renders the first; banners, rail dots and Inbox render the second. The UI never infers a prompt from the stream by itself; it shows what the daemon reports, so the daemon's own rules and the person see the same thing.

### 4.2 Live stream

The pane attaches to the terminal and receives rendered output continuously, with an initial snapshot of the screen and scrollback on attach, and resize from the pane. Detach never stops the agent. Several viewers may watch one terminal (laptop and phone); resize is owned by the last one that took the keyboard, and restored when it leaves (Orca does this for the phone, S20).

### 4.3 Who may type

**Target: several people typing in one session. v1: one input holder per session**, with the model written so that many is a change of limit, not of design.

A terminal has an **input holder**: `daemon` (default) or a named person/device. Taking the keyboard is explicit (button "Take control", or first keystroke in the pane after a confirm), and while held:

- the daemon does not deliver tasks or press keys there; work stays queued with the existing "held" mark and shows as such in D;
- the header shows who holds (`input: YOU`) and a timer; releasing, or idle for N minutes, hands back to the daemon;
- the daemon's own typing in the stream is shown in a faint marker line so the person can tell it from the agent's output.

Chat messages are not typing: they go through the daemon's delivery and need no hold. Key chips and typing in the pane are typing, and the UI always takes explicitly (`input.take`) first; it does not rely on the API's short hold for a non-holder.

Kept open for many (not built in v1):
- the holder is a *list* in the data model (`holders: [{who, since}]`, length 1 in v1); the UI always renders a row of avatars, with one in v1;
- a second person who asks gets "held by X" with a *Request* button; the holder's UI shows the ask and may hand over. v1 stops there;
- multi-typing later adds cursor/attribution in the stream and a conflict rule (last key wins vs. turns); the daemon-pause rule stays as is while any person holds.
- all viewers already see the same stream (§4.2), so watching with several people needs nothing new.

### 4.4 Safety

- Stop ▸ Kill and Close terminal ask to confirm, and Close too when the agent has work in flight (F13 kept); they say whether the agent has queued work.
- Yes on a permission prompt sends the single key the prompt's highlighted option needs, and only if the prompt is still the one the person saw (the request carries the prompt's id; if it changed, nothing is sent and the banner refreshes). Same for No.
- Everything a person does here is recorded as an event with who and when, visible on the agent's timeline.

### 4.5 Right panel (context for the selected agent)

Tabs: **Timeline** (state changes, tasks delivered, handoffs, key presses by the daemon and by people), **Task** (current request and its time box), **Worktree** (branch, changed files), **Notes** (agent memory index, read-only). Keeps the agent page free of everything that is not the conversation and the terminal. It can be closed.

## 5. Information architecture

```
/                          redirect to /inbox if anything waits, else /agents
/agents                    rail + welcome (team overview)
/agents/:name              Agent page (B)      ?tab=timeline|task|worktree|notes
/agents/:name/terminal     fullscreen pane (also the mobile landscape view)
/inbox                     C
/tasks   /tasks/:id        D, E
/team                      F
/accounts  /accounts/:n    G
/worktrees /worktrees/:id  H
/schedules                 J
/stats                     K
/settings/:section         L
```

Navigation depth is two: destination, then item. Modals: new agent (I), new task, confirmations, login. Deep links work for every item so a notification can open its exact place.

## 6. Orca features: kept, adapted, dropped

| Orca | Verdict | Why |
|---|---|---|
| F01 F03 F05 project/worktree/agent sidebar | **adapted**: agent-first rail | our unit is the agent |
| F04 parent/child | **kept** where spawned | host already records it |
| F06 workspace options | **reduced** to group/search/hide | fewer layouts to maintain |
| F07 create worktree dialog | **adapted** (I) | agent registration is part of it |
| F08 issue/PR/URL as context | **dropped** | no tracker in our system; tasks are `hl task` |
| F09 board | **kept** in Worktrees (H) | status field exists |
| F02 add project, F10 F11 where/naming | **kept** as Settings + dialog | needed to start work |
| F12 branch auto-rename | **dropped** | daemon concern, not UI |
| F13 confirmations | **kept** | destructive actions on live agents |
| F14 open in other apps | **dropped** in web; **Electron-only** | needs the local desktop |
| F15 worktree actions | kept as palette verbs | |
| F16 tab bar | **kept**, tabs are agents and tasks; no browser/emulator tabs | |
| F17 terminal settings | **kept, small**: font, theme, scrollback, copy-on-select | |
| F18 setup script, F19 quick commands | **adapted**: quick commands become *saved messages* for the chat box (per agent or global) | common phrases to Claude ("continue", "run the tests") |
| F20 palette | **kept** | |
| F21 shortcut editor | **dropped** for v1; fixed keys | cost vs. value |
| F22 F23 input, tab behaviour | partly kept | |
| F24 floating workspace | **dropped** | Agent page pane area suffices; reconsider with Electron |
| F25 appearance | **kept** (theme, zoom, terminal theme) | |
| F26 built-in browser, F27 agent browser use | **dropped** | web app lives in a browser; agents' browser use is the host's |
| F28 emulator, F29 Computer Use, F30 macOS permissions | **dropped** | out of the supervising job |
| F31 agent registry | **adapted** (I, Team) | roles and models, no vendor menu |
| F32 F33 F34 hooks, permission mode, trust | **shown, not edited** in Settings; trust folders kept | the daemon owns them |
| F35 Codex server | **dropped** | Claude only |
| F36 tab titles from prompt | **dropped** | agent names are stable |
| F37 keep awake | **kept** as a status-bar item | useful on a Mac host |
| F38 prompt-cache timer | **kept** on the rail row and agent-page header | tells when a resume is warm |
| F39 native chat UI | **kept for v1**, as the bridge's chat (§3.2) | founder decision 2: chat first, Claude Code terminal openable beside it |
| F40 agent sleep | **shown**: idle-close state | daemon already does it |
| F41 dashboard, pet | **dropped**; Inbox and rail serve | |
| F42 orchestration | **replaced** by Tasks and Team | ours is `hl task` |
| F43 voice | **deferred** (mobile input) | |
| F44 F45 accounts, usage | **kept, promoted** (G, status bar) | limits move agents |
| F46 stats | **kept**, no log scan (K) | |
| F47 session search | **deferred** | needs an index; palette searches names and tasks |
| F48 F49 F50 task sources, integrations | **dropped** | no trackers |
| F51 F52 F53 automations | **adapted** to Schedules (J) | `hl schedule` |
| F54 AI commit/PR text | **dropped** | agents do it |
| F55 SSH hosts, F56 remote servers | **deferred** | design 05 says another machine needs a filesystem story first; v1 reach is LAN/Tailscale to one daemon (§7, §8) |
| F57 Orca Mobile pairing | **adapted**: paired devices in Settings (§7); **no relay** in v1 | LAN/Tailscale only (founder decision 3) |
| F58 F59 F60 account, artifacts, share skills | **dropped** | |
| F61..F63 status bar, resources, ports | **kept**: status bar, resources popup; ports **dropped** | |
| F64 notifications | **kept** | |
| F65 plugins, F66 privacy, F67 help, F68 settings shell, F69 chrome | plugins dropped; the rest kept | |

## 7. Mobile screens (design only)

Same destinations, one column, bottom tab bar: **Inbox · Agents · Tasks · Accounts · More**.

1. **Inbox**: cards with inline Yes/No for permission asks, which is the main reason to open the phone. Push notification opens the exact card.
2. **Agents**: list rows as in §3.1, state first, swipe for Stop/Resume.
3. **Agent page** (portrait): header (name, state, account) · two tabs, **Chat** and **Terminal**. Chat is the default: messages, cards with inline Yes/No, the message box. Terminal shows the bottom rows (read-focused, pinch to zoom) with the **key strip** above the keyboard: Esc, ^C, ↵, ↑ ↓, ⇥, ⇧⇥, 1 2 3. A permission card also sits on the Chat tab, so it is reachable without opening the terminal.
4. **Agent page** (landscape): the Terminal tab fullscreen (`/agents/:name/terminal`), controls on a collapsible edge.
5. **Task**: list and detail, New task with voice dictation later.
6. **Accounts**: window gauges with reset time; cap and re-login are desktop-only (the login needs a terminal).
7. **Pair device**: QR from Settings on desktop, device list with revoke; shows the connection path (LAN or Tailscale; there is no relay in v1; the QR carries the certificate fingerprint). Input holder is shown as `PHONE` while held; taking control from a phone requires a confirm.
8. **More**: Team (read), Schedules (read), Stats, Settings subset (notifications, theme).

Not on mobile: Worktrees board, diffs, New agent. Terminal resize for the phone follows §4.2 and is restored on leaving.

## 8. Data each screen needs from the daemon (input for the API contract)

Names are descriptive, not final. Screen B's chat needs its own list, below the table. **Live** = pushed as it changes; **Snapshot** = fetched.

| Screen | Needs | Kind |
|---|---|---|
| Frame / status bar | daemon alive, version; per-account window use, reset, source mark; counts of agents by state; terminal host name and health; inbox count | Live |
| A Rail | per agent: name, role, model, effort, state, state-since, block kind and since, account, current task id and title, window use of its account, prompt-cache deadline, has-dialog flag, parent agent | Live |
| B Agent page: terminal | agent detail (above plus worktree path/branch, terminal handle, session id, session open/closed); **terminal stream** (a text grid in v1: snapshot of screen rows and scrollback on attach, then row updates, resize events); **parsed prompt** (kind, id, tool, command, reason, lines, offered keys); input holders (list; one in v1) and since; recent timeline | Live |
| B Agent page: chat | see "Chat data" below | Live + Command |
| B actions | start (agent, optional account), stop (interrupt / exit / close / kill), resume, send text (with or without Enter), send key (named key), answer prompt (prompt id, yes/no), take / release input, ask for input (reserved), resize; each returns accepted/refused with the reason, and carries a request id for safe retry | Command |
| C Inbox | items: kind, id, agent, title, body, evidence, task id, created, read/answered, options for a decision, prompt payload; actions: mark read, answer, reply | Live + Command |
| D E Tasks | tasks: id, from, to, title, body, state, relation, response mode, time box, created/delivered/done times, evidence, hold or block reason, delivery history, linked events; actions: new, withdraw, cancel, edit | Snapshot + Live |
| F Team | agents with roles, allowed edges, ROLE.md and base.md metadata, HR proposals with diff; actions: approve / reject proposal | Snapshot |
| G Accounts | per account: name, login state, 5-hour and weekly use with reset times and source mark, cap, agents on it, recent moves; actions: set cap, start login (returns a terminal to attach to), re-login | Live |
| H Worktrees | repos; worktrees: path, branch, owner agent, parent/child, status, changed files, diff on demand; action: set status | Snapshot |
| I New agent | repos, roles, models, accounts offered, free names; action: register + create worktree + start (with progress) | Command |
| J Schedules | schedules: id, target, cron, message, next/last run, who set it; actions founder-only | Snapshot |
| K Stats | counts and sums over 24 h/7 d: tasks done, agent working time, moves, handoffs, blocks; per account use | Snapshot |
| L Settings | daemon timings, host choice and health, trust folders, hooks installed state, paired devices with last seen; actions: change, revoke | Snapshot + Command |
| Notifications | event feed: task done, prompt asked, stall, account out, with deep-link target | Live |
| Everywhere | who is the viewer (person/device), event record of each action with actor | |

Cross-cutting needs for the contract:

1. **Authentication and device identity**: the web app is a remote surface on a daemon that today answers on a local unix socket; it needs a login, per-device tokens, and revocation. **Reach in v1: LAN or Tailscale only**: the daemon listens on a chosen interface (default loopback, opt-in for a LAN or Tailscale address; Tailscale is only a bind address, not a different security mode), always speaks TLS (an encrypted WebSocket, certificate fingerprint carried in the pairing QR, §7), and has no relay and no public endpoint.
2. **One event stream with resume**: a client that reconnects asks for events after an id and does not miss a prompt.
3. **Terminal stream as a separate channel** from state events (high volume, per terminal, with backpressure).
4. **Input holder rule** (§4.3), with the daemon refusing to type while a person holds.
5. **Prompt ids**: answers name the prompt they answer; stale answers are refused.
6. **Idempotent commands** (request id), as the host CLI already does.
7. **Permissions by role**: some actions are the founder's only (schedules, ROLE changes, roster); the API must say who may do what, and the UI greys the rest.

### Chat data (screen B, from the bridge's chat model)

1. **Messages of a chat**, paged: per request id, created time, time of its last state change, requester, target, state, body, answer (id, text, time), dismissed flag, `delivered_at`/`typed_at`/`landed_at`, parent id, kind, linked task ids and title, whether it is a task question. Scopes: *mine* (person and agent) and *all* (everything the agent sent or received). Paging: newest N, older before id, newer since id.
2. **Landing state per message** (queued, typed, delivered, delivered-not-answered) with the reason a message is still sending.
3. **The agent's side of the conversation and its work**: reply text and tool activity from the transcript, as ordered items with ids, so a client can append and not redraw; a working/waiting line (what it waits for or is blocked by); whether a turn is running.
4. **Prompt cards in the stream**: the permission or question prompt (id, tool, command, reason, options) and whether it is still open, answerable through the same call as in §3.2.
5. **Task events inline**: task opened, changed, done with evidence, withdrawn, with their request ids.
6. **Live updates**: new messages, landing-state changes and working-state changes pushed on the `chat` topic of the event stream with a resume id, so a chat open on two devices agrees.
7. **Send**: a message to an agent (body, optional task fields: title, time box), idempotent by request id, and answer, dismiss, withdraw on a message.
8. **Search and jump**: given a message id, return the agent and tab (*mine* or *all*) whose chat shows it.

### Reserved for diff review (not designed)

Worktree changes: changed files with status, and a unified diff per file on demand, read-only; a place to hang comments later. The UI shows Review only when the daemon's method list from `hello`/`whoami` includes the diff method; no separate flag.

## 9. Decisions (founder) and what is still open

Decided:

1. **Typing**: the target is several people typing; v1 has one input holder per session; the model stays open to many (§4.3).
2. **Chat**: v1 has a chat UI like the bridge's, with Claude Code and shell terminals openable in a pane that docks to any side or merges into tabs (§3.2).
3. **Reach**: LAN or Tailscale only in v1; no internet relay (§7, §8).
4. **Diff review**: yes in this app, later; reserved in §3.7 and §8, not designed.

Closed by the API spec (checked against its text and types):

- **Stop modes**: one meaning of close; Close terminal is its own method, refused while a session runs, so the UI stops first (§3.2).
- **Account preview**: `account_pick` on `agent.get`.
- **Free-text answer to a dialog**: `prompt.answer` takes a free-text choice; `session.input` stays refused while a dialog is open.
- **Chat**: paged messages with landing state, transcript, send, answer/dismiss/withdraw, locate and search, and a `chat` topic (the earlier `requests` topic is replaced by `chat` and `inbox`).
- **Holders**: `AgentDetail.input_holders` is a list; `input.ask` is reserved and refused until implemented, as this document reserves it.

**Styled terminal rows** are specified (format `styled`, `contract/protocol.md`); the daemon side is not built, and until it is the text grid is plain.

Open, smaller: where the Electron wrapper differs (native notifications, open-in-editor); whether a person's chat messages carry a task as the bridge's can (§8 chat data 7 assumes yes); how long a hold lasts idle before it returns to the daemon.
