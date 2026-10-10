# PC app gaps against the Handline bridge

The founder: apps/web "still lacks much of the Handline bridge's functionality". This lists what is missing, ranked by what the founder uses most, with the daemon method and the work unit for each.

Compared on 2026-10-06: the bridge is `handline/ui/` on main @3cd99e69 (the screen `hl` opens; Textual); the web app is `apps/web` on pc-build @c5492593 (unit 3). Feature list from `docs/orca-feature-inventory.md`, layout from `docs/web-main-view.md`, screens from `docs/web-ui-design.md`. Methods are those of `contract/protocol.json` (111).

## 1. What apps/web has and lacks

Status on 2026-10-11: see §4.1. The web app has every unit in §4. The phone app has the agent list and page, Chat, prompt cards, the Inbox, the agent verbs, agent settings and schedules, Accounts, the task verbs, HR, Team, Stats and Home settings; what it lacks is four small items in §4.1.

## 2. How the ranking was made

From the founder's own record on this machine (counts, not content), 2026-09-24 to 10-06:

- `requests`: **2,344** requests from `human`, of which **2,161** are chat messages (`say`) and 183 are tasks. Chat is by far the main thing the founder does.
- `chat_page.log` (what the founder did through the chat page and the task view), completed actions only (one action writes two or three lines; these are the lines that say `ok`): permission answers 68, dialog answers 37, questions 14, task notes 181, memos 40, discards 9, done 13, closes 10, agent settings saved 38.
- Not logged, so ranked by what the home screen puts first: account gauges, agent row verbs (wake, close, restart, park, fold, move), HR proposals, stats.

## 3. Gaps, in rank order

**Daemon** column: S = served by the real daemon now; the T-number is the plan task that builds it, from the daemon team's answer of 2026-10-06 to a list of 59 contract methods the Python daemon does not list. Of the contract's 111 methods, 56 are served on main: 52 by the Python daemon and `devices.list` `device.pair` `device.redeem` `device.revoke` by the Go listener; those four are in the list of 59 but need no work, so 55 are queued. The D-labels are in the legend below the table. **Listed** says whether that list covers the method; "no" is a new request (§5).

| # | Feature | Bridge evidence | Daemon method | Listed | Web unit | Electron |
|---|---|---|---|---|---|---|
| G1 | **Chat with an agent**: messages with landing state (sending, delivered, not answered), *mine* and *all* tabs, send, reply to one message, withdraw, ⚡ send now, older pages, the agent's replies and tool lines, task events inline, a working line, a draft per agent and ↑ history | `chat_page.py:804` (message), `:755` (working line), `:826` (send now), `:1679` (tabs, history), `:2376` (reply), `:2494` (cancel) | `chat.messages` `chat.transcript` `chat.send` `chat.answer` `chat.dismiss` `chat.withdraw` and topic `chat`: D1, D2; `capabilities.chat` flips with D2 | yes; **send now** no (N1) | U8 | E1 |
| G2 | **Inbox ("FOR YOU")**: results, questions to the founder, blocks; answer, answer in an editor, dismiss, mark read, mark all read, open the task or chat it came from | `tui.py:80` (keys), `home.py:155` (panel), `question_modal.py` | `inbox.list` S; `inbox.mark_read` `inbox.answer` `inbox.reply`: D4; `inbox.get`: D6; topic `inbox` | yes | U10 | E3 |
| G3 | **Prompts the agent is stuck on**: permission (Yes / No, never "always"), AskUserQuestion with numbered options, multi-select and "type something", dialogs with "always" (saves a standing rule); an answer to a prompt that has gone is refused ("the dialog is gone or changed") | `permission_modal.py`, `question_modal.py`, `chat_page.py` log kinds permission / question / dialog / confirm | `prompt.list` `prompt.answer` S; `answer.add` `answers.list` `answer.remove`: D6 | yes; multi-select flag no (N6) | U9 | E2 |
| G4 | **Tasks**: list and tree of what waits on what, filters (state, agent, closed), sort, detail with body, delivery history, evidence, progress and memos; new, assign a note, done, reopen, queue / draft, discard with a reason, change owner, priority, merge, bulk marks | `tasks_view.py:175-186` (12 keys), `chat_page.py:1623-1628` (task detail keys), `:1172` (`TaskActions`) | `tasks.list` `task.get` `task.new` `task.withdraw` `task.cancel` `task.edit` S; `task.board` `task.done` `task.reopen` `task.queue` `task.draft` `task.discard` `task.merge` `task.release`: D3; `task.memo.*`: D4; `task.progress` `task.handoff` `task.begin` `task.start`: D5 | yes; **priority** no (N2) | U11 | none |
| G5 | **Agent verbs on a row**: wake, close, restart (fresh or resuming), park, fold into its handoff, move to another account, clear a stall (asks first) | `tui.py:74` (`AGENT_KEYS`), `:87` (`CONFIRM`), `chat_page.py:872` (`/` commands) | `session.wake` `session.stop` `session.restart` `session.park` S; `agent.fold` `agent.move`: D5 | yes (`agent.unstall`: N4, fake only) | U12 | none |
| G6 | **Agent settings and its schedules**: model, effort, weight, hand off at, handoff note, repo, mode, sandbox, browser and sites, idle close, session account; add / edit / remove schedules | `agent_settings.py:70-121`, `schedules_section.py` | `agent.set`: D5; `schedules.list`, `settings.get`: D7; `schedule.add` `schedule.edit` `schedule.remove` `settings.set` S | yes | U13 | none |
| G7 | **Accounts**: gauges (5 h, week, reset, runway), who is on which, a page per account with its agents and settings (alias, cap, usage recovery, month cycle), login, add, remove | `home.py:148` (panel), `account.py:34,39,60-100`, `tui.py:79` | `accounts.list`: D7; `account.set` `account.set_cap` `account.login` S | yes; **remove** no (N3) | U14 | none |
| G8 | **HR proposals**: see the diff, approve, approve all, reject, edit the proposal's settings; a dot on the tab when one waits | `proposals.py:65`, `hr_card.py`, `home.py:562` | `hr.list`: D7; `hr.decide` S | yes | U15 | E2 |
| G9 | **Team**: who may ask whom, roles (read) | `hl team` (`handline/cli/team.py`): who may ask whom and what each is doing; the bridge has no team screen | `team.get`: D7 | yes | U15 | none |
| G10 | **Stats and outlook**: the week left, recorded and guessed; cost per day; ranges 12 h to 30 d | `outlook_view.py:159-190`, `stats.py` | `stats.get` D7 (24 h / 7 d only) | partly; **outlook** no (N5) | U16 | none |
| G11 | **Home settings**: default cap, idle close, standing answers (trusted folders, dialog rules) | `home.py:168`, `answer_add.py` | `settings.get` D7; `settings.set` S; `answers.*` D6 | yes | U16 | none |
| G12 | **The session's handoff**: a mark where each session started, click to read the handoff it was given | `chat_page.py:718` (`SessionMark`), `:734` (`HandoffModal`) | none in the contract (N7) | no | U8 (after N7) | none |
| G13 | **Open a path from a message; compose in `$EDITOR`; restart on update; one screen at a time** | `bridge_textual.py:71` (open path), `tui.py:80` (answer in `$EDITOR`), `bridge_textual.py:107` (restart on update), `:138` (one screen at a time) | none (client-side) | n/a | none | E3, E4, E1 |

Legend (the daemon team's plan groups): D1 chat reads and the `chat` topic; D2 chat writes; D3 task verbs; D4 task memos and inbox writes; D5 the agent-facing surface (`agent.set`, `task.progress` and others); D6 `inbox.get` and standing answers; D7 read methods part 2 (accounts, team, proposals, schedules, stats, settings). D1–D7 are one queue on the Python daemon; the plan is to relay them through the Go front door (no relay exists on main yet); `devices.*` is Go-only and already served. Build order given: D3 and D4, then D1 and D2, then D5, D6, D7 (D7 can move earlier on request).

## 4. Work units

Numbered on from `docs/turborepo-layout.md` §6 (units 1–7). Each needs unit 4a's responsive shell and ends with the root gate green, a test against the fake daemon, and the auditor. A unit builds only what `hello.methods` lists and shows "this daemon does not serve <thing>" otherwise (as `web-main-view.md` §3 does for tabs), so none waits for a daemon task to start.

**Data layer first.** Every unit reads and writes through `@handline/app-data` (React Query; `docs/turborepo-layout.md` §8), and every unit's *done when* includes that document's §9 checklist for its views: skeleton on first load, empty and error states, an optimistic or pending write for each action, rollback on refusal. Units Q1 (the package) and Q2 (`apps/web`'s existing reads and writes onto it) are in that document's §6 and **come before U8**; Q3 (mobile onto the same layer) follows Q1, and Q4 (removing what the cache replaced in the store) waits for both apps.

| Unit | Builds (web) | Done when | Needs | Can start |
|---|---|---|---|---|
| U8 | Chat tab (G1, G12): message list with landing state and *mine* / *all*, send box with draft kept per agent, reply, withdraw, send now, paging, transcript items, working line, topic updates; session marks | the Chat tab renders the contract fixtures for each message state and transcript kind; a reload keeps the draft; two tabs agree through the `chat` topic; the card for an open prompt is U9's | D1, D2; N1, N7 | reads now, against the fake daemon |
| U9 | Prompt cards (G3) in Chat and Inbox: permission, dialog, question with options and free text, "always" rules, stale-prompt refusal shown | every kind in a fixture answers through `prompt.answer`; a prompt that changed under the click sends nothing and the card refreshes; "always" appears only for dialogs | `prompt.answer` S; D6 for "always"; N6 | now, minus "always" |
| U10 | Inbox page (G2), bell count from `inbox` topic, deep links | list, filter by kind and agent, mark read, answer a decision, reply; a notice opens the card | `inbox.list` S; D4, D6 | list now; actions with D4 |
| U11 | Tasks page and detail (G4): table and tree, filters, bulk marks, new task, and every action of the bridge's two key tables | each key of `tasks_view.py` and each of the task detail's has a control and a test; confirmations match the bridge's | D3, D4, D5; N2 | list, detail, new, withdraw, cancel, edit now |
| U12 | The ⋯ menu gains park, fold, move, restart fresh, clear stall (G5) | one control per `AGENT_KEYS` verb; the confirm set equals `CONFIRM` | D5; N4 | park, wake, restart now; clear stall on the fake daemon only |
| U13 | Agent settings and schedules (G6) as a sidebar detail block's "Settings" view | each field of `agent_settings.py` edits and saves; the founder-only ones are greyed for other roles | D5, D7 | with D5 |
| U14 | Accounts page (G7) and the status bar's usage popup | gauges, per-account page, cap, alias, login opens a terminal tab (the web never holds credentials), remove | D7; N3 | with D7 |
| U15 | HR and Team (G8, G9) | approve / reject round trip; the tab dot matches the pending count | D7 | with D7 |
| U16 | Stats and Home settings (G10, G11) | counts of 24 h and 7 d; default cap, idle close and standing answers edit and save; the outlook graph only with N5 | D7, D6; N5 | with D7 |


Skeleton and writes per unit (the shape is a spec in `app-data`, shared with mobile; the rules are in `turborepo-layout.md` §8.5–8.6; **authored** writes show at once, **decided** ones show a pending mark):

| Unit | Skeleton on first load | Writes |
|---|---|---|
| U8 Chat | as the Chat tab in `web-main-view.md` §9: three message blocks, then the box | send, withdraw, dismiss, answer: authored; `chat.send_now`: decided |
| U9 Prompt cards | none (a card exists only for a reported prompt) | `prompt.answer`: decided; `answer.add`: authored |
| U10 Inbox | 4 cards: a title line, two body lines, an action row | mark read, answer, reply: authored (the bell count drops at once) |
| U11 Tasks | table of 10 rows (id, from → to, a wide title, a state chip, age); detail: title, a meta row, three body paragraphs, a history list | new, edit, done, reopen, queue, draft, merge, memo: authored; discard keeps its confirmation and is decided, as is a transition the app does not know |
| U12 Agent verbs | the sidebar's (`web-main-view.md` §9) | park, wake, restart, fold, move, clear stall: decided |
| U13 Agent settings | a form: 8 label and input rows; schedules: 3 rows | `agent.set`, schedules add / edit / remove: authored |
| U14 Accounts | 3 gauge cards (name, two bars, a reset line); account page: overview block, agents list, settings form | alias, cap, recovery, cycle: authored; login, remove: decided |
| U15 HR and Team | proposal cards with a diff block; team table of 8 rows | approve, reject: decided |
| U16 Stats and Home settings | 4 count tiles and a chart area; settings rows; answers list of 4 rows | settings, standing answers: authored |

Order: **Q1, Q2, then U8 + U9, then U10, then U11**, because that is the order of use in §2 and of the daemon plan (chat comes after the task verbs). U12 and U14 next, U13, U15, U16 last. U8, U9 and U10 can run in parallel (separate files; one cache).

| Unit | Electron | Done when |
|---|---|---|
| E1 | Shell: one window, one instance (a second launch focuses it, as the bridge's `_one_bridge` offers take-over), the dock badge shows the Inbox count | `apps/desktop` launches the built `apps/web`; a second launch focuses the first |
| E2 | Native notifications for `prompt_asked`, `task_done`, `stall`, an HR proposal and an account out; none while that agent's workspace is on screen; a click opens the card (U9, U10) | the `notifications` topic raises one native notice per event; a click lands on the deep link |
| E3 | A path in a message opens in the default app; "answer / compose in editor" opens `$VISUAL` / `$EDITOR` on a temp file and reads it back | both work from Chat and Inbox |
| E4 | Updates: when a new build is ready, ask "Restart now?" and do not ask while a draft or a send is open (the bridge's `_idle`) | declined once is not asked again for that build |

E1 can start once unit 4a lands; E2 needs U9 and U10; E3 needs U8 and U10; E4 any time after E1.

### 4.1 Status on 2026-10-11

Read on main `74162cb9`. "Built" means the code is there and a test names it; the unit's *done when* was not rerun. The phone has no unit numbers in §4: its column says which web unit it matches. Web files are in `apps/web/src`, phone files in `apps/mobile/src/screens`, desktop files in `apps/electron/src`.

| Unit | Web | Phone |
|---|---|---|
| Q1 `app-data` | built: `packages/app-data/src` (reads.ts, writes.ts, chat.ts, skeletons.ts), `test/guards.test.ts` | same package |
| Q2 / Q3 / Q4 apps onto it | built: `test/q2.test.tsx`; the store keeps nothing the cache owns (`64c0ee3b`) | built: `useWrite`, `Loadable.tsx`, `skeleton.tsx` |
| U8 Chat, session marks | built: `ChatTab.tsx`, `ChatCompose.tsx`, `ChatMessages.tsx` (send now, receipts, session mark with Whole handoff, tool output with Show all and the count of lines the store cut) | built: `Chat.tsx` (landing state and receipts, mine / all, send with now / queue, reply, take back, answer, paging, session mark that opens its handoff), `Transcript.tsx` (structured agents), `e2e/chat.yaml`; a tool line shows no output (see What is left) |
| U9 Prompt cards | built: `PromptCard.tsx`, `test/u9.test.tsx` | built: `PromptCard.tsx` (permission without "always", dialog "always" only when `answer.add` is served, question with free text, multi-select with Submit, stale prompt), `test/prompts.test.tsx` (`e94bdf45`) |
| U10 Inbox | built: `Inbox.tsx`, `InboxCard.tsx` | built: `Inbox.tsx` (answer, reply, mark read), `e2e/inbox-tasks.yaml` |
| U11 Tasks | built: `Tasks.tsx`, `TaskDetail.tsx` (memo edit and delete), `TaskBar.tsx`, `NewTask.tsx`, `taskActions.ts` | built: `Tasks.tsx`, `TaskActions.tsx` (new, done, reopen, memo add, withdraw, cancel, queue, draft, discard, edit), `test/screens.test.tsx`, `e2e/task-verbs.yaml`; no start, tree, merge or bulk marks, left off a phone on purpose; no memo edit or delete |
| U12 Agent verbs | built: `AgentVerbs.tsx` (wake, restart, close, park, fold, move, clear stall; Interrupt for a structured agent), `test/u12.test.tsx`. Clear stall is served by the fake daemon only (N4); elsewhere it is off with its reason | built: `AgentVerbs.tsx` (wake, restart, park, fold, move; close and clear stall hidden on purpose; Interrupt is on `AgentPage.tsx`), `test/verbs.test.tsx`, `e2e/agent-verbs.yaml` |
| U13 Settings, schedules | built: `SettingsView.tsx`, `Schedules.tsx`, `test/u13.test.tsx` | built: `AgentSettings.tsx`, `Schedules.tsx`, reached from the "Settings and schedules" button in the ⋯ list of `AgentVerbs.tsx`, `test/agent-settings.test.tsx` |
| U14 Accounts, usage popup | built: `Accounts.tsx`, `AccountSettings.tsx`, `AccountRemove.tsx`, `UsagePopup.tsx`, `Gauge.tsx` | built: `Accounts.tsx` (gauges, alias, cap, remove), `test/accounts.test.tsx`, `e2e/accounts.yaml`; no login (a phone never holds credentials) |
| U15 HR, Team | built: `HrTab.tsx` (diff, approve, approve all, reject, Reject all with one reason, Edit settings), `TeamPage.tsx`, `test/u15.test.tsx` | built: `Hr.tsx` (approve, Approve all, reject, withdraw, Edit settings; founder only), `Team.tsx`, `TeamEntry.tsx`, `test/hr.test.tsx`; no Reject all |
| U16 Stats, Home settings | built: `Stats.tsx`, `Outlook.tsx`, `Settings.tsx`, `Answers.tsx`, `test/stats-settings.test.tsx` | built: `Stats.tsx` (counts, agent time, outlook by range; its Stats / Settings switch is the founder's only way to `Settings.tsx`: default cap, idle close, switches, standing answers), the Stats tab in `Home.tsx`, `test/stats.test.tsx` (`4a590631`); no e2e flow |
| E1 Shell, E2 notices, E3 open / editor, E4 updates | built: `main.ts` one instance and dock badge, `notices.ts`, `desk.ts`, `open.ts`, `editor.ts`, `update.ts` (asks only when idle) | n/a |
| Connect / own modes | built: `mode.ts`, `own.ts`, `bridge.ts`, `tray.ts`; the pairing screen takes an address and code (`Start.tsx`); `README.md`; `DaemonDown.tsx` says the daemon is not running and how to start it | n/a |
| Structured agents | built: a page with Chat and Transcript and no terminal (`642d68b9`); the fake runs the real shape (`9aa7868e`) | built: `Transcript.tsx`, Interrupt beside the working line, no terminal (`aef8eb51`, `be304279`) |

**What is left**

- Web: nothing buildable. Clear stall stays off on dev and real daemons by the founder's decision (N4).
- PC app: nothing in §4.
- Phone, small and in order of use. Each ends with the root gate green, a test on the fake daemon, and the auditor.
  1. **Tool output in Chat.** `Chat.tsx` `Line` (`kind === 'tool'`) prints the tool and its text on two lines and drops `output` and `dropped_lines`. Done when a tool line with output opens it in full, says how many lines the store cut, and `test/transcript.test.tsx` has a case for both.
  2. **Memo edit and delete.** `TaskActions.tsx` `MemoBox` only adds; `useMemos` rows have no controls. Build as the web's `TaskDetail.tsx:291` with the `task.memo.edit` and `task.memo.delete` writes (already in `app-data/src/writes.ts`), off with their reason when the daemon does not list them. Done when a memo edits and deletes (delete asks first, as on the web) and a refusal rolls it back, in `test/screens.test.tsx`.
  3. **HR Reject all.** `Hr.tsx` rejects one proposal at a time. Add Reject all with one reason, as `HrTab.tsx:59`. Done when one reason rejects every waiting proposal and `test/hr.test.tsx` shows it.
  4. **E2E flows for the screens without one:** Stats and Settings, prompt answers, agent settings. `apps/mobile/e2e/` has none for them (the others have a yaml). Done when each has a flow that runs on the simulator with the fake daemon.
- Not gaps: the phone's own "Disconnected" banner stands in for `DaemonDown.tsx`, because a phone never starts a daemon.

## 5. Requests to the daemon team: missing from the contract and their list, or pending a decision

| # | Request | Why | Status 2026-10-11 |
|---|---|---|---|
| N1 | `chat.send_now { request }`: type a queued message now | the ⚡ button (`chat_page.py:826`); the contract has no such field, the table has `send_now` | in the contract and served (`rpc_chat_now.py:38`); web uses it (`ChatMessages.tsx:159`) |
| N2 | `Task.priority` (1 high … 3 low) on the row, and `priority` on `task.edit` (`task.new` and `task.begin` already take it) | the `p` key and the sort column in `tasks_view.py` | in the contract (`Task.priority`, `task.edit`); web edits and sorts it (`TaskDetail.tsx:330`, `TaskBar.tsx:21`) |
| N3 | `account.remove { account }`, founder only, with a dry-run that says which agents sit on it; the spec lists `hl account remove` as founder-only, pending the founder's decision | `x` on an account row | in the contract and served (`rpc_accounts.py`); web `AccountRemove.tsx` and phone `Accounts.tsx` use it |
| N4 | `agent.unstall { agent }`, founder only; the spec listed `hl unstall` as founder-only; the founder allowed it in the test environment, not in dev or real | wake on a STALLED row, with its confirm (`tui.py:87`) | in the contract and the fake daemon only; the Python and Go daemons do not list it, so the web shows Clear stall off there (`AgentVerbs.tsx`) |
| N5 | `stats.outlook { range }`: week left, runway, cost per day | `outlook_view.py`; `Stats` has only 24 h / 7 d counts and two percentages | in the contract and served (`rpc_outlook.py:63`); web shows it (`Outlook.tsx`) |
| N6 | `Prompt.multi: boolean` (select several, then submit) | AskUserQuestion's multi-select (`question_modal.py`); `PromptOption` can say each option, not that several may be picked | in the contract (`Prompt.multi`); web and phone use it (`PromptCard.tsx`) |
| N7 | a `session` transcript item: session id, started, and the handoff text it began with | `SessionMark` and `HandoffModal` | in the contract (a `session` transcript item); web shows it (`ChatMessages.tsx:266`) |

## 6. Not gaps

- Orca's browser, emulator and Markdown tabs: decided in `web-main-view.md` (R1–R5).
- New agent and Worktrees screens, design §3.7–3.8: the bridge has no such screen (`hl spawn` is a command), so they stay design work, after U16.
- The bridge's `$EDITOR` and open-path actions are not web features; E3 only.

## 7. Open points

- **The ranking's ground is thin below G4.** Accounts, HR, stats and the row verbs are used from the home screen, which logs nothing. If the founder ranks them differently, only the unit order changes.
- **Quick commands.** Design §6 turns F19 into saved messages; the bridge has none. Left out of U8 until asked for.
- The D-labels' states move; re-ask the daemon team before starting a unit.
