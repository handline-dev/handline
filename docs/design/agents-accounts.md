# Screen designs: agent verbs, agent settings and accounts

Layout, states and keys for units U12 (the `⋯` menu), U13 (agent settings and schedules) and U14 (accounts page and the status bar usage popup) of `docs/pc-app-gaps.md` §4, inside the frame of `docs/web-main-view.md` §2 (every agent control lives in the sidebar). Same form and shared rules as `docs/design/inbox-tasks-prompts.md` §1: tokens from `apps/web/src/styles.css` (no new colour), skeleton with `docs/turborepo-layout.md` §8.6 timing, the four list states, the key sheet on `?`, phone under 640 px. Write kinds are those of `turborepo-layout.md` §8.5: **authored** (the value changes at once, hollow dot `○`, reverts on refusal) and **decided** (no cache change, "…" on the clicked control, reason on error). Docs only: nothing here is built.

Two items wait on a founder decision and are drawn but marked **Pending**: clear stall (N4) and account remove (N3). They are hidden, not greyed, until the daemon lists the method and the founder has decided; the design is ready so the unit does not wait for it.

## 1. U12 The `⋯` menu

The menu of `web-main-view.md` §2 holds Interrupt, Exit, Close, Kill, Close terminal, Move account, Rename. U12 makes it carry exactly the bridge's agent verbs (`tui.py` `AGENT_KEYS`), by the same words:

| Verb | Bridge key | Call | Kind | Confirm | Shown when |
|---|---|---|---|---|---|
| Wake | `w` | `session.wake` | decided | only when the row is stalled (the bridge's `_needs_confirm`); otherwise none | no session or parked |
| Close | `c` | `session.stop` | decided | yes | a session exists |
| Restart | `r` | `session.restart` | decided | yes | a session exists |
| Restart fresh | (the bridge's restart without resuming) | `session.restart { fresh: true }` (the contract has `agent`, `fresh`, `force`; Close fresh is `session.stop { mode: close, fresh }`) | decided | yes, and the sheet says the session's context is dropped | a session exists |
| Park | `p` | `session.park` | decided | yes | a session exists |
| Fold | `f` | `agent.fold` | decided | yes, and the sheet names the handoff it folds into | a session exists |
| Move account | `v` | `agent.move` | decided | none: choosing the account is the choice | always |
| Clear stall **(pending, N4)** | the stalled row's `w` | `agent.unstall` | decided | yes | state STALLED, founder only |

The confirm set is the bridge's `CONFIRM` (close, restart, park, fold, cancel) plus wake-on-stalled (clear stall). Move is not in it. Kill and Close terminal keep their existing confirmations (`web-main-view.md` §2). All are decided writes: the row shows its pending mark in the state cell with the bridge's gerund ("closing…", "parking…", "folding…", "waking…", "restarting…"), the `⋯` stays openable but the verb in flight is disabled, and the row changes only when the `agents` topic says so. Refusals show the daemon's reason in a toast named by the verb.

**Confirmation.** An inline sheet under the row (not a modal, as in the Inbox doc): the verb and agent in one line ("Park handline.x?"), one sentence of consequence in `--muted` ("Its session ends; it wakes on the next task."), **Confirm** (primary, Enter) and **Cancel** (Escape). The sheet re-checks on Confirm: if the agent's state changed while it was open, nothing is sent and the sheet says "It changed while you looked" and closes (the bridge's `_confirmed_can`). The state a verb applies to is read from the daemon's `agents` data, so a menu opened on a stale row never offers a verb the state cannot take.

**Menu layout.** Order: primary verbs first (Wake or Resume, Close, Restart), then Park, Fold, Move account, a rule, Interrupt, Exit, Rename, a rule, the destructive ones (Kill, Close terminal) in `--block`. Each item shows its bridge key at the right as `<kbd>` when the row has focus in the sidebar; the keys work on the focused row without opening the menu, and `?` lists them. A verb that does not apply is hidden, not greyed; the founder-only item (clear stall) is hidden for other roles from `whoami`.

**States.** Loading: the sidebar's own row skeleton; the `⋯` is absent until the row has data. Not served: a verb whose method is not in `hello.methods` (agent.fold, agent.move today, until D5) is hidden; if none of the extras is served the menu is today's. Error: toast plus the mark removed. Disconnected: items disabled with "daemon not connected".

**Phone.** `⋯` opens a bottom sheet with the same list; items are 48 px, destructive ones last and separated; the confirmation is a second sheet page with full-width Confirm and Cancel.

## 2. U13 Agent settings and schedules

A **Settings** view of the sidebar's detail block (`web-main-view.md` §2): the block shows the facts read-only; "Settings" opens the view in the main area as a route `/agents/:name/settings`, one page, no tabs (the bridge's page is one scroll with section heads).

### 2.1 Layout

A single column, 640 px at most, label at left (160 px) and control at right, section heads in `--muted` small caps as the bridge's `SectionHead`. Sections and fields are those of `agent_settings.py`, in order:

- **Agent** (read-only): session runs on (account), vendor, enabled.
- **Model**: model (pick from the known list or type one), effort (select), weight (select), hand off at (number, "k tokens", with the share of the window it is shown beside it), handoff note (select; the "follow the global switch" choice names what the switch says now, e.g. "follow the global switch (on)").
- **Work**: repo (text).
- **Session**: mode (select, bypass not offered), skip prompts (switch, "from the next launch"), sandbox (switch).
- **Browser**: browser (select), sites (a list of allowed sites with add and remove), idle close (minutes, 0 = never).
- **Schedules** (§2.3).

Fields whose effect comes at the next launch carry the bridge's note ("from the next launch") in `--muted` beside the control. Each field has its own note line under it for the validation message, in `--block`, never a toast.

### 2.2 How saving works

Write kind from §8.5: `agent.set` is **authored**. The web keeps the bridge's per-field save (its `commit()` on blur for text fields, on change for selects and switches); there is no Save button, because the cache patch is per field and a failed field reverts alone:
- A text or number field saves on blur or Enter; a select and a switch save on change.
- While the value differs from the daemon's, the field shows the hollow dot; on refusal it reverts and its note line says why. A field with an invalid value (a non-number in "hand off at") is not sent; the note says what is allowed.
- **Two switches confirm first, in the bridge's words.** Turning **skip prompts** on asks "Stop asking before this agent runs tools": "skip prompts runs claude --dangerously-skip-permissions: it will act without asking, from the next launch.", buttons **Turn on** and **Cancel**, drawn in `--block` as a danger confirm. Turning **sandbox** off (when it is on) asks "Run this agent without its sandbox": "Its commands will run with nothing holding them back, until its session ends.", buttons **Turn off** and **Cancel**. Until the person confirms nothing is sent; on Cancel, or a refusal, the switch snaps back to its stored value. Turning either the safe way (skip prompts off, sandbox on) asks nothing.
- The founder-only fields (from `whoami`) show a lock `🔒` and are read-only for other roles, with "founder only" as the tooltip; the page never offers a write it knows will be refused.
- Leaving the page with an unsaved invalid field asks "Leave without saving?" with the problems listed (the bridge's `Confirm.ask` on leave); with only valid fields there is nothing unsaved.

### 2.3 Schedules

The schedule methods are founder-only in the contract: for other roles (from `whoami`) the controls are hidden and the list is read-only, as the locked fields of §2.2.

A list inside the page, one line each: when in words ("every weekday 09:00"; the cron under it in `--mono` and `--muted` when the words differ), next run, the message (one line, cut with …), and `✎` edit and `✕` remove buttons. "Add schedule" under the list. Add and edit open an inline sheet with a when field (cron, with the words rendered live under it), a message box, Save and Cancel. Writes `schedule.add` `schedule.edit` `schedule.remove` are authored (§8.5): the line appears or changes at once with the hollow dot, and reverts on refusal. Remove keeps the bridge's confirmation, naming the message's first words.

### 2.4 States

- **Loading**: field-shaped skeleton (label bar and control bar per row, five section heads) at the real row height; the page title is shown at once.
- **Error**: the strip of the Inbox doc §1; fields keep the last values, read-only until it clears. A schedules error (`schedules.list`) shows in that section only: "cannot read the schedules: <reason>" and Retry.
- **Not served**: `schedules.list` missing hides the Schedules section; `agent.set` missing makes the whole page read-only with "this daemon does not edit agent settings".
- **Empty**: schedules "none" in `--muted` plus the Add button.
- **A change from elsewhere** (another tab, the bridge) replaces the field unless the field has focus; a focused field keeps what is typed and shows "changed elsewhere: <value>" with a Use button.

### 2.5 Keys and phone

Keys: Tab and Shift-Tab between fields, Enter saves a text field, Escape reverts the focused field to the stored value or, with none changed, goes back; `?` lists them. No letter keys, since a settings form is text. Phone: one column, label above control, 44 px controls, section heads sticky; schedule add and edit are a full-screen sheet; the page is reached from the agent's sheet ("Settings") since there is no sidebar block.

## 3. U14 Accounts page and the usage popup

### 3.1 The status bar usage popup

The status bar item "acct alpha 41% · beta 12%" (`web-ui-design.md` §2) opens a popup anchored above it (a bottom sheet on phones): one line per account: alias or name, the **5 h** gauge, the **week** gauge, the resets in words ("resets in 2 h 10 m"), agents on it ("3 / cap 4"), runway. A gauge is a bar in `--panel` with the fill in `--run`, `--wait` from 60% and `--block` from 85% (the bridge's `gauge.py` thresholds), a tick `│` for how far through the window we are (the bridge's bar), and the percentage as text, so colour is never alone. A row is a link to that account's page. At the foot: "All accounts" opens the page, and when the daemon serves it, `stats.outlook` figures (the week left) are shown, otherwise omitted (N5).

Narrow popup widths drop things in the bridge's order (`_ACCT_TIERS`): first the reset countdowns, then the percentages beside the bars. Loading: three account skeleton rows; empty: "No accounts configured." with a pointer to the login; error and not served as in §1 of the Inbox doc; the last data stays visible during a refetch. The popup refreshes from the `accounts` topic; no polling interval is drawn.

### 3.2 Accounts page

Route `/accounts` (the top-bar destination), `/accounts/:name` for one account.

**List**: a card per account (a table on desktop at 1100 px or more: account, state, 5 h gauge, week gauge, resets, agents, runway): the same gauges as the popup, a state chip (in use, idle, out, login needed) with its word, and the agents on it. Selecting opens the account.

List keys (the bridge's `ACCOUNT_KEYS`): Enter open, `l` login, `a` add, `c` cap, `x` remove **(pending, N3)**.

**One account** (`/accounts/:name`), three tabs as the bridge (Overview, Agents, Settings):
- *Overview*: title, badge, meta (vendor, email when the vendor returns it, config dir), the usage block (5 h and week gauges with resets), the month block (when the subscription cycle ends), the recovery state, a status line.
- *Agents*: the agents on it with state and detail, each a link to its workspace.
- *Settings*: alias (blank shows the account's name), cap (a number; empty is the default), usage recovery (switch), "ends this month" (switch) and last day (`YYYY-MM-DD`; blank means the next monthly anniversary), vendor and config dir read-only. Each field saves on its own; `account.set` and `account.set_cap` are **authored** (§8.5): value at once with the hollow dot, reverted with the reason on refusal. Founder-only fields lock as in §2.2.

**Login** (`l`, `account.login`, **decided**): the web never holds credentials. The button opens a terminal tab (`web-main-view.md` §3, a shell kind) in which the vendor's own login runs; the account row shows "login in progress" with the decided pending mark until the terminal's session ends and the `accounts` topic reports the new state. If the daemon cannot open a terminal (R2 absent), the button is hidden and the page says "log in from the host: `hl account login <name>`". On the phone login is desktop-only and the button is absent (`web-ui-design.md` §7).

**Add** (`a`): a sheet with the account name and vendor, then the same terminal login as above; the account appears in the list when the daemon creates it.

**Remove (pending, N3).** Designed for when the founder decides; hidden until the daemon lists `account.remove` and `whoami` says founder. Flow, in the bridge's order: Remove opens a dry run (the call's plan) first. If agents sit on the account the sheet lists them ("2 agents are on it: <names>") and offers no confirm, only Close. Otherwise it asks the person to **type the account's name** to confirm, then sends; the daemon checks again after the confirmation, because an agent may have started meanwhile, and its refusal is shown as the reason. A decided write: "removing…" on the button, the row leaves when the topic says so.

### 3.3 States (page)

- **Loading**: gauge-shaped skeletons (title bar, two bar rows) at the real heights; the top bar and the tabs are shown at once.
- **Empty**: "No accounts yet." with Add; **Error** and **not served** as the Inbox doc §1; a gone account: "This account no longer exists" with a Back link.
- **An account that is out** (limit reached): the card's chip reads "out until <time>" in `--block` and its gauges stay; agents on it show their own state.
- **Pending marks**: as §1 of the Inbox doc; login and remove are decided, edits authored.

### 3.4 Phone

The list is cards with the two gauges stacked and the agents count; the account page is one scroll with Overview first, Agents and Settings behind a segmented control; Settings fields as §2.5. Login, add and remove are not offered on the phone (they need a terminal or a founder at a desk); the page says "Log in from a computer" where the button would be.

## 4. What the tests check

- U12: one control per `AGENT_KEYS` verb; the confirm set equals the bridge's `CONFIRM` plus wake-on-stalled; a confirm whose state changed sends nothing; a verb the daemon does not serve is absent; decided marks per verb. Clear stall is built behind the method being listed.
- U13: each field of `agent_settings.py` edits and saves, reverts on refusal with its note; turning skip prompts on and sandbox off ask first, and a cancelled or refused switch snaps back and sends nothing; founder-only fields lock; schedules add, edit and remove with the hollow dot; skeleton heights equal loaded heights.
- U14: gauge colours at 59, 60, 84 and 85 percent with the text always present; the popup's narrow tiers; login opens a terminal tab and never asks for a credential; remove, when listed, refuses an account in use before it asks for the name.
- All: skeleton timing of §8.6, no spinner in the DOM, light and dark, reduced motion, screenshots at 375 and 1280.

## 5. Assumptions to confirm

1. **Pending, founder:** clear stall (N4) and account remove (N3) are founder-only in the spec and undecided; the designs above hold either way, and "founder only" hides them from other roles.
2. Login by terminal tab depends on R2 (terminals the web can open); without it login is a host command.
3. Move account has no confirmation, as in the bridge; the account chosen is shown before the call.
