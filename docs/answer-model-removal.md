# Removing the answer model

Survey of main `56f2c4e3` for items 3, 4 and 5 of [removal-candidates](removal-candidates.md). Nothing is removed here.

- **3. Question cards:** the open, answered and dismissed states, `chat.dismiss` and `isOpenAsk` go. A question that needs an answer is a task.
- **4. Inbox options:** the `option` param of `inbox.answer`, `DecisionOption` and `InboxItem.options` go.
- **5. Response kinds:** `async` / `fire_and_forget`, `relation`, `reply` and `waits_on` go. The only link between agents is "may ask": an edge, or a default link from the name.

## What each piece becomes

| Today | After |
|---|---|
| An edge's `response` decides whether the asker waits for a reply. `close.go` `AfterClose` wakes an `async` asker when its request closes. | A link only allows a message. Work that needs an answer is a task, and `task done` already tells the opener (`taskwrites/end.go` notice row). |
| `reply`: the routing lets an agent answer whoever asked it, with no edge back (`team.Route(..., reply)`, `supervisor.RepliesTo`). | Gone. An answer goes back through a link or through the task (done, handoff, memo). The live team.yaml has `* -> human`, and the default links cover parent, child and siblings. |
| `hl say --parent N` settles request N: an answer row (`requests.settles`) is pushed to the asker as an answer (`PushAnswer`). | A say with a parent link, typed like any say. Nothing is settled or pushed. |
| `ChatMessage.landing` can be `delivered_unanswered`, `answered` or `dismissed`; `answer`, `dismissed`, `is_task_question`; `ChatRequestItem.dismissal`. | Landing is `queued`, `typed`, `delivered` or `withdrawn`. Read or not is `chat.seen`. |
| `chat.dismiss` takes an ask off the mailbox. | Nothing to dismiss: a say is read, a task is done or discarded. |
| `inbox.answer` decides a decision task, answers a question, or replies to an answer. | A decision is the founder's task, closed with `task.done` on `InboxItem.task` (`decide` already ends in `taskwrites.MarkDone`). A reply is `inbox.reply`: a say with the parent link that settles nothing. |
| `InboxItem.answered`; the founder's mailbox keeps its own line for a sender that "waits on the reply" (`mailRow.waiting`: `response = 'async'`). | The item's task state says whether it is decided. A decision task keeps its own line because it is a task (`kind = 'task'`). |
| `hl say` that needs an answer. | `hl say` already "needs no answer". Its help and `skill/SKILL.md` point to `hl task new --assign`; nothing is added. |
| `waits_on` on `task.edit` (free text, never served by Go). | Nothing. What a task waits on is `--blocked-by` (`task_blocks`) and its open children, which `taskview.waitsOn` and the task clock already read. |
| The stop gate reminds an agent of an unanswered question (`session/stopgate._unanswered_question`). | Gone, with the rest of item 3. A question that must be answered is a task, which the wait check already holds the agent to. |

## Removal list by layer

### Contract (`contract/`)
- `protocol.json` methods: `chat.dismiss`, and `inbox.answer` (its decision case moves to `task.done`). Params to drop: `task.new` `relation`, `response` and `reply`; `task.edit` `waits_on`. Text to fix: `inbox.reply` ("settling it"), `request.read` `wait_s` ("until answered"), `inbox.queue` ("a request whose sender waits on the reply"), `chat.requests` (dismissal).
- `protocol.json` schemas: `Landing` loses `delivered_unanswered`, `answered` and `dismissed`. `ChatMessage` loses `answer`, `dismissed` and `is_task_question`; `ChatRequestItem` loses `dismissal`. `InboxItem` loses `answered` and `options`. `DecisionOption` goes. `Task` loses `relation` and `response`; `HrArgs` loses `response` and `relation`.
- Fixtures: `chat_writes.json` (`chat.dismiss.*`), `memos_inbox.json` (3 `inbox.answer.*`), `tasks.json` (`task.edit.waits_on_is_not_served`), `request_read.json` (`relation`, `response`, `dismissal`), `reads.json` (`Task` relation/response, `InboxItem` answered/options), `chat_parent.json` (its "answered" wording).
- Generated, never edited by hand: `protocol.md`, `protocol.ts`, `go/internal/rpc/contract.json`.

### Fake daemon (`packages/fake-daemon`)
- `src/chat.ts`: `chat.dismiss`, `dismiss()`, the `delivered_unanswered` and `answered` landings, `is_task_question`, and a question kept queued until it is answered.
- `src/inbox.ts`: the `inbox.answer` case, `answered`, `options` and `dismiss`. `src/daemon.ts:374`: the `inboxDismiss` hook. `src/tasks.ts`: `response`. `src/fixtures.ts`: the seeded `answered`, `delivered_unanswered` and task-question rows. `test/knobs.test.ts`: the answered and dismiss tests.

### Go daemon (`go/internal`)
- Routing, `team/team.go`: `validResponse`, `Decision.Relation` and `Decision.Response`, `Edge.Relation` and `Edge.Response`, the `reply` param of `Route`, the relation in `DefaultLink`, and the `StarterTeam` edges. The loader must *ignore* `relation` and `response` keys, not refuse them (see Live risks).
- HR, `hrwrites/`: `doers.go` (`validResponse`, `response`/`relation` in the link proposal), `teamyaml.go` (`linkText` writes `response:`/`relation:`; `relationRe`), `store.go` `subjectFields`. `reads/hrtext.go:255` prints "(response …, relation …)".
- Sending, `supervisor/`: `ask.go` (`RepliesTo` use, `closeReplied`, `Settled`/`SettledTell`, the `relation`/`response` columns and event fields, "(response=%s)" in the reply), `delivery.go` (`RepliesTo`), `close.go` `AfterClose` (the `async` wake) and `ResolveIn.DismissedBy`, `push.go` and `sends.go` (`PushAnswer*`), `sendpath.go` `announced`. `Response: "fire_and_forget"` is passed at about 12 call sites (`ask.go`, `digest.go`, `systemsay.go`, `tasks.go`, `sendpath.go`, `compose/tick.go`, `taskwrites/end.go`, `taskwrites/new.go:263`).
- Chat and mailbox, `chatwrites/`: `actions.go` (`AnswerWrite`, `IsAnswerToHuman`, `DismissWrite`; `decide` stays as the `task.done` path), `chatwrites.go` `dismiss`, `inbox.go` (`inboxAnswer`, `mailRow.waiting`, the `settles IS NULL` filter).
- Reads, `reads/`: `chat.go` (`dismissal`, the `answered`/`delivered_unanswered` branches of `landingOf` and `landingState`, `answer`, `task_question`), `inbox.go` (`answered`, `options`, `waiting`, `answerSQL`), `inboxget.go:95` and `tasks.go:336` (`relation`, `response`).
- Db, `db/requests.go`: `answerSQL`, `closingSQL`, `dismissedSQL`, `LegacyDismiss`, `Request.Answer`, `AnsweredAt`, `Dismisser` and `DismissReason`, `Dismissal()`, `AddAnswer`, `AnswerRowOf`, `AppendVerdict`, `OpenAskIDs`, `UnannouncedAnswers`, `MarkAnnounced`, and `CloseOpts.DismissedBy` and `AnsweredBy`. `db/tasks.go`: `DecisionTaskOfCard` and `SettleDecisionTasks` (a decision ends by `task.done`, not by its card).
- Tasks, `taskwrites/new.go:67` and `edit.go:243`: `unserved(..., "relation", "response", "reply", "waits_on")`. With the params gone from the contract, an old caller's extra param is ignored, like any unknown param.
- Not part of this: `cmd/hl/taskview.go` `waitsOn` (blockers and children), the prompt `options` (`Prompt`, `prompt.answer`) and `structured/session.go` `response` (the CLI's control protocol).

### Go hl (`go/cmd/hl`)
- Little to change. `say.go` and `task.go` send none of the removed params. `task_test.go:204` uses `waitsOn` (kept). The `--parent` wording in `say.go`'s comment and help follows.

### Python hl and hook path (`handline/`)
These are live:
- `cli/parser.py`: `hr link --response/--relation` (lines 202–204), `say --parent` help ("the reply settles it"), `read --wait` help ("until it has been answered"), `task new --reply` (575). `cli/task.py:238` passes `--reply` on, and Go refuses it today.
- `cli/ask.py:277`: `cmd_read` prints "relation … response …". `cli/team.py` and `cli/hr.py`: the response text.
- `team/team.py`: `VALID_RESPONSE`, `Decision`, `route` (reply), `default_link` (relation); the loader must ignore the keys, as in Go. `team/hr.py` (`_link_text`, `_edge_refusal`, `_decision_text`); `team/linkmigrate.py` (prints the relation).
- `session/stopgate.py`: `_unanswered_question` and its `answer_reminded` meta.
- `db/schema.py`: the migration source for Go (`tools/go_schema.py`). Add `relation`, `response` and `settles` to `_UNREAD_REQUEST_COLUMNS` once nothing reads them; then `hl doctor --drop-dead-columns` drops them. A step never drops a column (schema.py's rule).

Step 4c takes these, so they are not edited here unless a slice needs it: `team/ask.py`, `actions/requests.py` (`dismiss`, `answer_ok`, `founder_waits`), `actions/tasks.py` (`decide`, `_request_answered`), `db/requests.py`, `db/tasks.py`, `daemon/delivery.py` (`deliver_answers`, `answer_line`).

### Tests
- Python: `tests/team/test_tasks_requests.py` (94 hits, most of them `fire_and_forget` edges in setups), `tests/team/test_hr_agents.py` (45), `tests/support.py` (22, `_two_agents` edges), `tests/daemon/test_landing.py`, `test_sends.py`, `test_message_texts.py`, `tests/session/test_stopgate.py`, `tests/infra/test_contract.py`.
- Go: `chatwrites/chatwrites_test.go`, `chatwrites/messages_test.go`, `team/team_test.go`, `supervisor/ask_test.go`, `reads/requestread_test.go`, `reads/inboxqueue_test.go` with `testdata/inboxqueue/founder-mailbox-all.txt`, `hrwrites/hrwrites_test.go`, `db/requests_test.go`, and the `watcher/testdata/parity` recordings (re-record).

### Docs
`docs/configuration.md:32` (edge example), `docs/reference.md:41` (`--reply`), `design/02-team.md`, `docs/electron-bridge.md`, `docs/design/inbox-tasks-prompts.md`, `docs/contract-requests.md`, `docs/daemon-plan.md`, `docs/turborepo-layout.md:195`, `docs/pc-app-gaps.md`, `docs/web-ui-design.md`, `docs/direct-writers.md`, and rows 3–5 of `docs/removal-candidates.md`. `AGENTS.md` and `skill/` have no hits.

## Slices, in landing order

Each slice is one review.

1. **Daemon tolerates the old spelling.** The Go and Python team loaders ignore `relation` and `response` on an edge, and `hr.propose` ignores them in `HrArgs`. No behaviour changes for a team.yaml without them. *First, with nothing before it.*
2. **Contract + fake.** Drop the methods, params, fields and fixtures above. Regenerate the three generated files. Drop the Go `Register` lines of `chat.dismiss` and `inbox.answer` in the same commit, since `hello` lists from the generated `contract.json`. The fake follows. *After the web and mobile app slices (below) have shipped, because the apps read `answered`, `options`, `dismissed` and `answer` and call both methods.*
3. **Go daemon: routing and sending.** `team`, `hrwrites` and the `supervisor` pieces: no relation, response or reply, and no `async` wake. Before deleting the wake, confirm a Go test holds "the opener is woken when its task is done" through the done notice. *After 1.*
4. **Go daemon: chat, mailbox and db.** Answer rows and `settles`, dismissal, the landing branches, `mailRow.waiting`, and `DecisionTaskOfCard`/`SettleDecisionTasks` (a decision ends by `task.done`). Write NULL to `relation`, `response` and `settles`; no migration step. *After 2 and 3.*
5. **Go hl.** Help and comment wording only. *After 2.*
6. **Python hl.** Drop `hr link --response/--relation`, `task new --reply`, the read header, the stop-gate reminder and the help texts. Add the three columns to `_UNREAD_REQUEST_COLUMNS`. *After 4, so no daemon still reads the columns.*
7. **Docs and team.yaml.** Docs as listed. The live `team.yaml` loses its `relation:` and `response:` keys through a founder-approved `hl hr diff`; only the founder's path writes and seals it. *After 1 (either order with 2–6).*

## App files (pc builds apps/web, mobile builds apps/mobile)

The shared packages the apps import change with them:
- `packages/app-core/src/chat.ts`: `isOpenAsk`, `takeBack`'s answered landings, and the `landingLabel` entries. Tests: `test/chat.test.ts`, `test/tasks.test.ts`, `test/task-table.test.ts` (`response: 'async'`).
- `packages/app-data/src/chat-writes.ts` (`chat.dismiss`, the `dismissed` and `is_task_question` seeds), `src/chat.ts:84` (`dismiss`), `src/writes.ts` (`inbox.answer` patch; `response ?? 'async'` on an optimistic task). Test: `test/writes.test.tsx`.

apps/web (pc):
- Delete: the dismiss and answer verbs in `src/Inbox.tsx`, `src/InboxCardParts.tsx` (Dismiss button, answer box, option buttons) and `src/inboxKinds.ts`; `waits` in `src/InboxCard.tsx`. The Inbox's decision card calls `task.done` on `item.task` instead.
- Change: `src/ChatMessages.tsx` (answer and dismissed stamps, `i.open`), `src/ChatCompose.tsx` (`isOpenAsk`), `src/ChatTab.tsx` (header comment), `src/msgClass.ts` (`landing=dismissed`), `src/ui/Icon.tsx` (`answered`, `dismissed`, `unanswered`).
- Tests: `test/inbox.test.tsx`, `test/chat.test.tsx`.

apps/mobile (mobile):
- `src/screens/Chat.tsx`: the `delivered_unanswered`, `answered` and `dismissed` glyphs, `isOpenAsk`, the Dismiss button. `src/screens/Inbox.tsx`: `waits`, `inbox.answer` and `answered`; a decision becomes `task.done`.
- Test: `test/screens.test.tsx` (the answered-decision and dismiss cases).

## Live risks and how the order avoids them

- **The live team.yaml carries `relation:` and `response:` on every edge.** A loader that refused them would stop routing for the whole team. Slice 1 makes both loaders ignore the keys before anything stops writing them. The file is cleaned last, by the founder (slice 7). An old daemon reading a cleaned file defaults to `async` and keeps working.
- **An old hl sends a removed param.** The Go hl sends none of them. The Python hl sends `response`/`relation` (`hr link`) and `reply` (`task new --reply`). After slice 1 the daemon ignores the first two; `reply` is already refused. Slice 6 removes the flags afterwards.
- **Installed apps read fields that slice 2 drops.** The contract's own rule: removing a field or method changes the protocol, and the daemon serves old and new side by side for one release. Slice 2 waits until the pc and mobile builds no longer read them. A method missing from `hello` only hides a button (`useServes`); a missing `answered` would leave a decision showing "waiting" forever.
- **A daemon rolled back after slice 4** reads NULL `relation`, `response` and `settles`. No column is dropped until slice 6 lists them and the founder runs `hl doctor --drop-dead-columns`.
- **Requests still open at the switch.** An `async` request queued before slice 3 keeps its column value, but nothing wakes its asker from it any more. Asks are no longer created (every Go caller of `Ask` writes a task delivery or a say), so only task deliveries are open, and `task done` tells their openers.

## Open for the manager

- An edge's `kind` (`kind: task` on `* -> human`, the older `escalate`, `verdict`) is not in item 5. "Only may ask" suggests it goes too, with "a say to the founder is a say; work for the founder is `hl task new --assign human`". Not in these slices unless decided.
- `task.ask`, where the founder asks about a task: it is already a say linked to the task, so it stays. Only its `is_task_question` mark goes.
