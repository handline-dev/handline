# Story 9-0: "my turn", the menus, and the shapes (research, inventory, proposal)

Doc only (T3996). Read from the code at `origin/main` 499bf7b37. PC side only; questions for the phone are in section F.

## A. How others show "your turn"

| Product | Your-turn surface | Notifications vs decisions | Top menu kept small by | Limit warnings | Licence of what we may borrow |
|---|---|---|---|---|---|
| [Orca](https://github.com/stablyai/orca) (worktree agent IDE) | Notifies when an agent finishes or needs attention; threads can be marked unread to return later | One stream, status-driven; no approval/FYI split found | Not checked | None found | MIT (LICENSE read: Lovecast Inc., 2026). Code and assets may be copied with the notice |
| [Linear Inbox](https://linear.app/docs/inbox) | Inbox; new **Priority** tab splits "needs your attention" from "can wait" ([changelog 2026-09-03](https://linear.app/changelog/2026-09-03-priority-inbox)); Triage is a separate inbox for unreviewed items | Priority tab is filtered by source; snooze (H, see the [Inbox docs](https://linear.app/docs/inbox)) hides an item until a time, new activity cancels it | Few sidebar entries: Inbox, My issues, then teams | None | Proprietary: ideas only |
| [GitHub notifications](https://docs.github.com/en/account-and-profile/managing-subscriptions-and-notifications-on-github/viewing-and-triaging-notifications/managing-notifications-from-your-inbox) | Inbox with default filters (Assigned, Participating, Review requested, Mentions); each item has a `reason` | The `reason` is the split: `review-requested` and `assign` are decisions, `subscribed`/`state-change` are FYI; up to 15 saved filters | One bell | None | Proprietary: ideas only. The reason vocabulary is public docs |
| Cursor Agents window ([forum](https://forum.cursor.com/t/agents-approval-window/170388)) | Sidebar grouped by Status, with a **Needs Attention** section for agents blocked on approval, question or plan, plus finished work not yet reviewed | Status groups, not a feed | Grouping, not menus | Not found | Proprietary: ideas only |
| Conductor ([docs](https://www.conductor.build/docs)) | Workspace list showing who works and what needs attention | Not found in official docs | Not checked | Not found | Proprietary: ideas only |
| Claude Code | `/usage` shows 5 h and 7 day percentages; the status line gets `rate_limits` {five_hour, seven_day: used_percentage, resets_at} (Pro/Max only) | n/a | n/a | Percentages and reset time shown on demand; no official early warning found. Third-party tools pause at 90% (5 h) / 95% (week) ([example](https://www.claudepluginhub.com/plugins/willyt3hwhale-rate-limit-guard)) | Ideas only |

What carries over: a **reason on each item** (GitHub), **one place for what is mine now** apart from the rest (Linear Priority, Cursor Needs Attention), **snooze with reawakening on new activity** (Linear), thresholds at about 90% (5 h) and 95% (week) for the warning. Not found: any product that says "stops at HH:MM at this rate"; that is ours (section D, 9-3). Searched only the public web, once each; Conductor and Orca internals not read.

## B. Inventory today

PC = `apps/web` (also inside `apps/electron`; there is no `apps/desktop`). Phone = `apps/mobile`.

| Place | Name | Holds | PC | Phone |
|---|---|---|---|---|
| Left rail, top | Back / forward / hide list, bell | History, the unread count (opens Inbox) | yes | no |
| Left rail, nav | Inbox | Items waiting on the person; filters kind and agent | yes | tab |
| | Tasks | List/tree, task detail, New task, Edit, verbs | yes | tab |
| | Team (tabs Team, HR) | Who is who and may ask whom; HR proposals for the founder | yes | TeamEntry screen, HR screen |
| | Worktrees | Worktree board and panel | yes (if served) | page from Agents tab |
| | Accounts | Per account gauges, agents, login, remove | yes | tab |
| | Stats | Tiles (tasks done, moves, handoffs, blocks), agent time, Outlook | yes | tab |
| | Pair a phone | One-time QR window (Electron with own gateway only) | yes | n/a (Unpair button on Agents tab) |
| Left rail, list | Agent rows | State dot, needs_you chip, queue, cache and context, row menu (verbs) | yes | Agents tab |
| | Filter popover | Show agents by state | yes | no |
| | New agent (button) | `#/new`, operations and progress | yes | page from Agents tab |
| Left rail, foot | Settings | Daemon settings: Concurrency, Closing windows, Automation | yes | Settings screen |
| | Keys (?) | Key sheet | yes | no |
| Status bar | Daemon, agent counts by state | Read-only chips | yes | Banner when disconnected |
| | Usage popup | Per account 5 h and week gauges, agents, runway; links to Accounts | yes | no |
| Agent page | Tabs Chat, Tasks, Timeline, Transcript, Claude Code (terminal); split panes | Per agent | yes | Chat/Terminal tabs plus Tasks, Timeline |
| Agent settings (`#/agents/:n/settings`) | Sections Model, Work, Session, Browser; Schedules | One field per save | yes | AgentSettings |
| Palette (⌘K) | Pages, agents, tasks, messages | Search and jump to any page above | yes | no |
| Capture (⌘⇧C, desktop) | Capture dialog | Window picture to task or comment | yes (Electron) | no |
| Tray (Electron) | Open, daemon state, mode, Pair a phone, Quit, update | Menu bar | yes | n/a |
| Phone bottom bar | Agents, Inbox, Tasks, Accounts, Stats | Five tabs | n/a | yes |

Count today: the PC rail has 4 to 7 nav rows (Inbox, Tasks, Team, Accounts, Stats always; Worktrees if served; Pair a phone in Electron with its own gateway) plus 2 footer buttons, a status bar and a palette; the phone has 5 tabs.

## C. Fold into 2-3 top menus

Proposal: **My turn**, **Work**, **Team**. Settings and the account popover sit in the footer, not in the menu.

```
┌ rail ───────────────────────────┐
│ ◀ ▶            [bell 3]         │
│ ● My turn  3                    │  inbox + needs_you + HR waiting
│ ▣ Work                          │  Tasks, Agents (the list below), Worktrees
│ ☺ Team                          │  Team, HR
│ ─────────────────────────────── │
│  agent rows ...                 │
│  [+ New agent]                  │
│ ─────────────────────────────── │
│ ⚙ Settings   ?   ◔ main 62% ▾  │  account popover (9-3)
└─────────────────────────────────┘
```

| Today | Lands in |
|---|---|
| Inbox, HR proposals, agent `needs_you` chips | **My turn** (9-1, 9-2) |
| Tasks, Worktrees, agent page, New agent | **Work** (Tasks and Worktrees as tabs of one page) |
| Team table | **Team** |
| Accounts, Stats, status-bar usage | Account popover; its "All accounts" opens the Accounts page; Stats becomes a tab of it |
| Pair a phone | Settings > Devices (the Electron tray item stays) |
| Keys (?), Palette | Unchanged (keys, ⌘K) |
| Agent settings, Schedules | Unchanged (inside the agent page) |

Open for the founder: whether Worktrees stays visible as a top entry (the rail stays at 3 only if it does not).

## D. Shapes

**9-1 My-turn cards.** Ordered by task priority (P1 first), then by deadline (task clock `end`, soonest), then oldest. One primary action per card, the rest in a menu.

```
My turn · 3
┌────────────────────────────────────────────────┐
│ P1  Decision  T3912 ship the picker?   in 40m  │
│ handline.pc · asks: A (Tiptap) or B (Lexical)? │
│ [ A ] [ B ]  [ Reply ]  [ Snooze ▾ ]           │
├────────────────────────────────────────────────┤
│ P2  Approve   handline.web  Bash: rm -rf dist  │
│ waiting 6m                                      │
│ [ Allow ] [ Deny ]   [ Open terminal ]         │
├────────────────────────────────────────────────┤
│ P2  Accept    T3983 report ready   no deadline │
│ [ Accept ] [ Send back ]  [ Open ]             │
└────────────────────────────────────────────────┘
```

A `needs_you` of kind `dialog` (any dialog the daemon does not know) has no Allow/Deny: its card has only Open terminal.

**9-2 Inbox split.** Three groups; FYI is collapsed and shows a count.

```
Decisions  (2)    questions the agent cannot go on without
Approvals  (1)    permission prompts, task reports to accept
▸ FYI      (14)   results, messages, blocks that wait on others   [Mark all read]
```

**9-3 Account popover.** Headroom per account and a stop time.

```
◔ main  62% of 5 h used · week 41%
   agents stop at 17:40 at this rate (in 1 h 52 m) · resets 19:00
◔ second  12% · the window resets first: no stop time shown
   [ All accounts ]   [ Stats ]
```
`runs_out_in_seconds` is null when the reading is stale, there is no burn, or the window resets first; then the popover shows no stop time, not "safe". Warn (amber) from 90% of the 5 h window or when the stop time is before the reset; red from 95% or in under 15 minutes.

## E. What the daemon must add

Checked first: `NeedsYou` {kind permission/question/usage/dialog, since, text, source} is on AgentView; `InboxItem` has `kind` (a free string; the Go reader writes decision, ask, refused and the request kinds such as say, and the web also tests block and report/result), `prompt` (a field, kind permission/question inside it), `task`, `options`, `answered`, `read`; `Task` has `priority` and the clock in its `time` field (`end`, `overdue_since`, `left_s`; null once done); `Account` has `five_hour`/`weekly` {used_pct, resets_at}, `runway` (words, `~10m to wall`) and `Outlook.runway` {account, runs_out_in_seconds, week_resets_at} (from `stats.outlook`; `runs_out_in_seconds` is null when the reading is stale, there is no burn, or the window resets first). The web already draws runway from `gaugeOf`.

| Need | Exists | Add | Read by |
|---|---|---|---|
| Decision / approval / FYI split | `InboxItem.kind` (free string) | Nothing required: map kinds on the client (decision, ask = decisions; an item whose `prompt` is a permission = approval; the rest = FYI). Ask for a `needs` field (`decision` / `approval` / `fyi`) only so the daemon, not each client, owns the table | 9-2, phone |
| Priority and deadline on an item | on the task only | `InboxItem.priority` and `InboxItem.due` copied from its task (null without one), so a card needs no task read | 9-1 |
| Snooze | none | `inbox.snooze {id, until}` and a `snoozed_until` field; new activity clears it | 9-1 |
| Stop time | `Outlook.runway.runs_out_in_seconds` (from `stats.outlook`; null for a stale reading, no burn, or a reset first); `Account.runway` is words | `Account.runs_out_at` (Timestamp or null) in `accounts.list` with `usage: true`, so the popover needs one call and no text parsing | 9-3, phone |
| Warning level | none | Not needed: the client applies the thresholds in D | 9-3 |
| Items for "My turn" count | `inbox.list` + agent `needs_you` + `hr.list` | `daemon.status` field `my_turn` (count; it has `inbox.unread` today) so the badge costs one call. Optional | rail, tray |

## F. Questions for handline.mobile

1. Five tabs today (Agents, Inbox, Tasks, Accounts, Stats). Does the phone fold to the same three (My turn, Work, Team), or keep a bottom bar of its own?
2. Where does the phone show headroom: a header chip on every tab, or a sheet from the Accounts entry?
3. Can cards carry the action buttons (Allow, Deny, Accept, option buttons) on the phone with the same verbs as the web?
4. Does the phone get push for a new My-turn item, and is snooze wanted there?
5. Unpair stays at the foot of Agents today; does it move into Settings with Pair?
