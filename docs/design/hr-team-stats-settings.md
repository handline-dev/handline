# Screen designs: HR, Team, Stats and home settings

Layout, states and keys for units U15 (HR proposals and the Team table) and U16 (stats, home settings, standing answers) of `docs/pc-app-gaps.md` §4, in the frame of `docs/web-main-view.md`. Same form and shared rules as `docs/design/agents-accounts.md` and `docs/design/inbox-tasks-prompts.md` §1: tokens from `apps/web/src/styles.css` (no new colour), skeleton with `docs/turborepo-layout.md` §8.6 timing, the four list states, the key sheet on `?`, phone under 640 px. Write kinds are those of §8.5: **authored** (value changes at once, hollow dot `○`, reverts on refusal) and **decided** (no cache change, "…" on the clicked control, reason on error). Confirms, saves and thresholds below were read from the bridge (`proposals.py`, `hr_card.py`, `outlook_view.py`, `home.py` settings tab, `answer_add.py`), and role gates from `contract/protocol.json`. Docs only: nothing here is built.

**Role gates (contract).** `hr.list`, `team.get` and `stats.get` are readable by agents and members; `hr.decide`, `settings.get`, `settings.set`, `answers.list`, `answer.add`, `answer.remove`, `trust.remove` and `settings.trust` are **founder-only**. Rule for every page here: a control the role cannot use is hidden (an action) or locked `🔒` with "founder only" (a value shown read-only); the Settings page is not served to a non-founder ("Settings are for the founder.") because `settings.get` itself is founder-only.

**Pending:** the outlook graph and the longer stats ranges wait on **N5** (§3.2); they are drawn but hidden until `stats.outlook` is listed.

## 1. U15 HR proposals

### 1.1 Where it lives

Route `/team` has two tabs, **Team** and **HR**, and `/team/hr/:proposal` is a deep link to one card. The HR tab wears the bridge's dot while a proposal waits: a `●` in `--block` and the count as text ("HR 2"), shown on the tab, on the **Team** item of the top bar and in the status bar's host line, so it is seen from any page; colour is never alone. The count is the proposals whose state is `proposed` or `approved` (the founder's move: waiting, or approved but not applied), as the bridge's `open_proposals`; it comes from `hr.list` and refreshes from the topic. The same proposal also appears in Inbox as an HR card (`inbox-tasks-prompts.md` §3.1) and both show one state.

### 1.2 The HR tab

```
┌ HR · 2 waiting ───────────────────────────────────────────────── [Approve all] ┐
│ ┌ HR · spawn handline.x   waiting for you   from architect, 12 m ago ─────────┐ │
│ │ vendor claude · model … · team …            (the fields of the proposal)    │ │
│ │ why  <the reason, plain text>                                                │ │
│ │ ┌ diff ───────────────────────────────────────────────────────────────────┐ │ │
│ │ │ - effort  high                                                          │ │ │
│ │ │ + effort  max                                                           │ │ │
│ │ └─────────────────────────────────────────────────────────────────────────┘ │ │
│ │ [ Approve ]  [ Reject ]                                    [ Edit settings ] │ │
│ └──────────────────────────────────────────────────────────────────────────────┘ │
│ agents  agent · model · effort · browser · mode · sandbox     (read; Enter edits) │
```

One column of cards, oldest first, 760 px at most. Each card has: head (the kind and agent, the state word, who asked, how long ago); the asked fields in the bridge's order (vendor, model, effort, team, repo, to, then each `set <key>`); **why** as plain text; the **diff** (the contract's `diff`) in `--mono`, one line per change, a `-` or `+` at the line start so colour is not the only mark (`--block` and `--run` on top of it), scrolling past 20 lines, never rendered as HTML; then the buttons. Every value came from an agent, so it is shown as text only, one line per field, no markup, no links, control characters dropped (the bridge's `textsafe`).

State words are the bridge's `STATES`: "waiting for you" (`--wait`), "approved, apply failed" (`--block`, with the error under it, first 400 characters), "approved and applied" (`--run`), "rejected" (`--block`), "withdrawn" (`--muted`). Settled cards stay for the session's recent history under a "Recent" rule, with no buttons.

Below the cards, an **agents** table (agent, model, effort, browser, mode, sandbox) as the bridge's: a read-only list; Enter or **Edit settings** opens that agent's settings page (U13, `agents-accounts.md` §2). An unreadable agent shows "unreadable" in its row and does not hide the others.

### 1.3 Verbs, confirms and keys (the bridge's `ProposalsView`)

| Key | Verb | Call | Kind | Confirm |
|---|---|---|---|---|
| `a` | Approve | `hr.decide { decision: approved, subject }` | decided | none (the card is the confirmation); on a card in "apply failed" it reads **Retry** |
| `r` | Reject | `hr.decide { decision: rejected, note, subject }` | decided | a reason line opens under the buttons: "why - Enter rejects, Esc cancels"; an empty reason sends nothing |
| `A` | Approve all | one `hr.decide` per waiting proposal, in order | decided | yes: "Approve all N" / "Each is applied as the card shows it; one that fails stays waiting." |
| `w` | Withdraw | none | none | **not offered in the web** |
| `e` | Edit settings | opens U13 | none | none |

- **`subject`**: an approve or reject is for the text the card showed (the bridge's `subject`). If the proposal changed under the click, the daemon refuses as stale; nothing is applied, the card refreshes in place with "This proposal changed" in `--wait`, and the person decides again on the new text. Never auto-retried.
- **Withdraw** (`w`) is the proposer's call (`hr.withdraw` is on §8.5's list of agent commands not made from the UI), so the web has no Withdraw; the bridge's keeps working. The key sheet omits it.
- **Approve all** sends the calls one after another and shows each card's own "approving…" mark; a partial failure leaves the refused ones waiting, with "2 of 3 approved" and each reason in the toast. The bridge also sends each proposer one summary message; the contract has no batch call, so the web promises only what the daemon does for each `hr.decide`.
- `hr.decide` is **decided**: the card's two buttons read "…" and disable, the card changes only when the `hr` topic says so, and the dot count drops then. On refusal the buttons return and the reason shows in the card. The three decide verbs and Edit settings are hidden for a role that is not founder; the cards stay readable.
- Keys work only with no text field focused; `?` lists them; Escape closes the reject field, then leaves the page.

### 1.4 States

- **Loading**: three card skeletons (head bar, three field bars, a diff block, two button bars) at the real heights; the tab strip is shown at once.
- **Empty**: "No proposal is waiting for your decision." (the bridge's words), and under it "Roster and role changes agents propose land here." The tab's dot is off.
- **Error / not served**: the shared strip and the "not served" line; cached cards stay. Not served hides the HR tab.
- **Disconnected**: decide buttons disabled with "daemon not connected".
- **A new proposal** arrives at the top of "waiting" with the 2 s left rule of the Inbox doc §3.3, and collects behind a "1 new" pill when the list is scrolled.

### 1.5 Phone

HR has no tab on the phone: proposals are Inbox cards (`web-ui-design.md` §7), with Approve and Reject as full-width 48 px buttons, the diff collapsed to "3 changes" and a "Show diff" toggle, the reject reason as a bottom sheet. The unread count of Inbox carries them. Approve all and Edit settings are desktop-only.

## 2. U15 The Team table

Read-only (G9): `team.get` returns agents (name, role, parent, role file and base file with their update times) and edges (who may ask whom). The bridge has no team screen; `hl team` is per agent ("you may ask", "may ask you", what each is doing), and this page is the same facts for every agent at once.

**Table** (route `/team`, tab Team): columns agent, role, reports to, **doing** (the agent's state word and task from the `agents` data, as the sidebar row), role file updated, base file updated. Sort by a column head; a **tree** toggle groups by `parent` (indent, collapsible), the default when more than one root exists. A row expands to **may ask** (edges out) and **may be asked by** (edges in) as chips linking to those agents, so the "who may ask whom" of `hl team` is readable per agent; an agent with no edges says "nobody". Enter opens the agent's workspace; `t` switches table and tree; `?` lists keys. No editing: roles live in files the daemon reads; the "file updated" columns are there so a person sees a role changed. A role file that has never been written shows "not set" in `--muted`.

- **Loading**: eight row skeletons at the real row height with the real column widths.
- **Empty**: "No agents in the team." (a daemon with none); **error / not served** as the shared rule.
- **Phone**: cards: name and role on line one, "reports to" and the doing line under it, the expand control for the edges; no tree toggle (a flat list sorted by parent).

## 3. U16 Stats

### 3.1 Tiles and charts (what `stats.get` serves today)

Route `/stats`. Window switch **24 h | 7 d** (the contract's two windows; key `r` steps it). Tiles in a responsive grid, each a number in `--ink` and a label in `--muted`: **tasks done**, **moves**, **handoffs**, **blocks** (blocks above zero in `--wait`, the number always shown). Under the tiles two charts, both built from plain DOM bars with a text value on every bar, never colour alone:

- **Agent time**: a horizontal bar per agent from `working_seconds`, sorted longest first, the bar in `--accent`, the value as "3 h 12 m"; agents with zero are folded under "n idle agents".
- **Account use**: per account two bars, 5 h and week, from `per_account`, coloured by the bridge's gauge thresholds (`gauge.py`): `--run` under 60%, `--wait` from 60%, `--block` from 85%, with the percentage as text and "no data" in `--muted` where the daemon returns null. The same bands as the usage popup of `agents-accounts.md` §3.1.

A line under the title says the window's span from `since` and `until` ("12 Oct 14:00 to 13 Oct 14:00"). Both charts redraw on the window change without a skeleton (data is cached per window, and the previous window's figures stay until the new ones arrive).

### 3.2 Outlook graph (**pending N5**)

The bridge's `OutlookView`: the week left of the accounts, recorded and guessed, a ±range (12 h, 24 h, 3 d, 7 d, 14 d), and a cost-per-day view (7 d, 14 d, 30 d) drilled team to agents. None of it is served: `Stats` has two windows and two percentages (N5 asks for `stats.outlook { range }`). Designed so the unit does not wait:

- A **View** select ("session left" / "cost per day") and a **Range** select appear on the page only when `stats.outlook` is listed; until then the page shows tiles and the two charts of §3.1 and no placeholder.
- *Session left*: a line chart per account (SVG, `--accent`, the guessed part dashed and labelled "guess"), a legend, a data table behind a "Table" toggle so the numbers are readable; a marker for "now".
- *Cost per day*: stacked bars per day by team, the team's bar clicks into its agents, Backspace goes back (the bridge's `backspace`), values as a table behind the toggle.
- Keys (the bridge's): `v` view, `r` range, `Backspace` back from a team; `?` lists them.
- Loading: a chart-shaped skeleton (axis lines and three bar blocks) at the real chart height.

### 3.3 States and phone

- **Loading**: four tile skeletons and two chart skeletons, real heights; the window switch is live at once.
- **Empty** (a new daemon, nothing recorded): the tiles show 0 and the charts say "Nothing recorded in this window."
- **Error / not served**: the shared strip; a non-member sees nothing here (`stats.get` is member-level).
- **Phone**: tiles two across, charts stacked, the bar value labels stay; Stats is under More (`web-ui-design.md` §7); the window switch is a segmented control.

## 4. U16 Home settings and standing answers

Route `/settings` (the gear), founder only. One column, 640 px at most, label at left, control at right, section heads as `agents-accounts.md` §2.1. All values come from `settings.get` (`values`), and all writes are `settings.set { changes }`, **authored** (§8.5): the value changes at once with the hollow dot, and on refusal it reverts and the field's own note line says why (never a toast alone). Saves are per field as the bridge's: text on Enter or on leaving the field, switches on change.

**Concurrency**
- **Default cap** (`active_cap`, integer from 1): "agents working at once on an account". The bridge's note: an account with its own cap keeps it. Clearing the field sends `null` (back to the default). A non-number or 0 is not sent; the note says what is allowed.

**Closing windows**
- **Idle close** (`park_timeout_s`, shown in whole minutes, 0 = never): "minutes of idle before a session closes". Stored in seconds; the page converts and rejects a non-number the same way.

**Automation** (three switches, saved on change, no confirmation, as the bridge)
- **Handoff note**: "agents write a note for the next session before a close or move".
- **Auto-accept**: "answer the questions in the list below by itself" (`auto_accept_dialogs`).
- **Ignore cache expiry**: "resume the old session even after its cache went cold".

Per-agent overrides of idle close and cache expiry are the agent's own page (U13); this page is the default.

**Standing answers** (`answers.list`): a table of the auto-answers, one line each ("trust folder /path" or "<vendor>: <prompt title> → <option>"), with **Add** and **Remove selected** buttons under it, the bridge's two. **Add** opens a form with a kind select ("trust a folder" / "answer a dialog") and the fields of that kind: folder; or vendor, prompt title and the option to choose. Its Add button is the confirmation: the row appears at once with the hollow dot (`answer.add`, authored) and reverts on refusal; a form with every field empty sends nothing. **Remove** removes the row at once with no confirm (`answer.remove { kind, key }`, authored), and the row folds with a 6 s Undo that only delays the write, as the Inbox doc's dismiss (assumption 4). An empty list says "none - you are asked when a dialog shows" in `--muted`. The trusted-folder rows can also be removed through `trust.remove`; both leave the same list.

States: **loading**: field-shaped skeleton (label and control bars, four section heads, three list rows); **error**: the shared strip with fields read-only; **not served** (`settings.get` missing): "This daemon does not serve settings." A non-founder gets "Settings are for the founder." with no controls. A change from elsewhere replaces the field unless it is focused, in which case "changed elsewhere: <value>" and a Use button, as `agents-accounts.md` §2.4.

**Keys**: Tab and Shift-Tab between fields, Enter saves a text field, Escape reverts the focused field, `?` lists them; no letter keys. **Phone**: the founder's settings are not on the phone (§7 of the design gives it only notifications and theme); the page says "Open this on a computer."

## 5. What the tests check

- U15: HR card renders every state word from fixtures; the diff keeps its `+` and `-` marks with colour removed; approve and reject send `subject`, and a stale refusal sends nothing more and refreshes; an empty reason sends nothing; Approve all sends one call per proposal in order and shows partial failure; Withdraw is absent; the dot count equals waiting plus apply-failed proposals; decide controls are absent for a non-founder; Team rows show edges both ways.
- U16: tiles from a fixture of each window; gauge colours at 59, 60, 84 and 85 percent with the text always present; null percentages say "no data"; default cap `null` on empty; idle close converts minutes and seconds both ways; answer add and remove reverse on refusal; every founder-only control is absent for a member.
- All: skeleton timing and heights of §8.6, no spinner in the DOM, light and dark, reduced motion, screenshots at 375 and 1280.

## 6. Assumptions to confirm

1. HR and Team share the `/team` route as two tabs, with the HR dot on the Team item of the top bar. The alternative is a separate HR item in the top bar, which `web-ui-design.md` §2 does not list.
2. Approve all is N calls in order, so the bridge's one summary message per proposer is not promised (no batch call in the contract).
3. Withdraw is dropped from the web because `hr.withdraw` is an agent command in §8.5; if the founder wants it on the page, the daemon must list it for the founder.
4. Remove on a standing answer: the bridge removes at once with no confirm; the web adds a 6 s Undo that delays the write. If delaying is not wanted, the row is removed at once as the bridge does.
5. Auto-accept and the other switches save with no confirmation, as the bridge does, although auto-accept lets the daemon answer dialogs by rule. Whether it deserves a danger confirm like skip prompts is the founder's call.
6. Team stays read-only; role and base files are shown by path and update time and are not opened in the web.
7. Stats ranges stop at 24 h and 7 d until N5; the 12 h, 3 d and 14 d ranges of the bridge are not offered.
