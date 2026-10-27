<!-- Generated from contract/spec/ by contract/generate.py; do not edit. -->
# tasks methods (protocol 1)

A method marked write takes a required `request_id`. Framing, roles, errors and gates are in [protocol.md](../protocol.md); field types in `protocol.ts`.

| Method | Role | Write | Params | Result | Errors | Gates |
|---|---|---|---|---|---|---|
| `tasks.list` | agent |  | `owner?`, `reporter?`, `from?`, `state?`, `tags?`, `parent?`, `under?`, `before?`, `limit?`, `all?`, `everyone?`, `my_turn?`, `reason_gaps?` | `{items, more, reason_gaps} & ReadCursor` |  |  |
| `task.get` | agent |  | `task` | `TaskDetail & ReadCursor` | `not_found` |  |
| `task.history` | agent |  | `task` | `{task, rows}` | `not_found`, `bad_request` |  |
| `task.time` | agent |  | `task` | `{task, start, end, segments, estimate, expected} & ReadCursor` | `not_found`, `bad_request` |  |
| `task.shot` | member |  | `task`, `n`, `offset?` | `{mime, bytes, offset, data, done}` | `not_found`, `bad_request` |  |
| `task.new` | agent | yes | `owner`, `reporter?`, `title`, `body?`, `time_box?`, `parent?`, `no_parent?`, `duplicate_ok?`, `blocked_by?`, `watch?`, `priority?`, `estimate?`, `tags?`, `expects?`, `ui?`, `request_id` | `Accepted & {task}` | `refused` | duplicate_title |
| `task.handoff` | agent | yes | `task`, `to`, `note?`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.done` | agent | yes | `task`, `evidence?`, `request?`, `repo?`, `reason?`, `commit?`, `shots?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` | open_children |
| `task.reopen` | agent | yes | `task`, `note?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.progress` | agent (only agent) | yes | `task?`, `note`, `request_id` | `Accepted` | `refused`, `not_found` | not_self |
| `task.begin` | agent | yes | `title`, `body?`, `message`, `priority?`, `expects?`, `why?`, `request_id` | `Accepted & {task}` | `refused` |  |
| `task.start` | agent | yes | `task`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.comment.list` | agent |  | `task`, `before?`, `limit?` | `{memos, more}` | `not_found` |  |
| `task.memo.list` | agent |  | `task`, `before?`, `limit?` | `{memos, more}` | `not_found` |  |
| `task.comment.add` | agent | yes | `task`, `text`, `now?`, `reply?`, `files?`, `request_id` | `Accepted & {memo}` | `refused`, `not_found`, `bad_request` |  |
| `task.memo.add` | agent | yes | `task`, `text`, `now?`, `files?`, `request_id` | `Accepted & {memo}` | `refused`, `not_found`, `bad_request` |  |
| `task.comment.edit` | agent | yes | `task`, `memo`, `text`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.memo.edit` | agent | yes | `task`, `memo`, `text`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.comment.delete` | agent | yes | `task`, `memo`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.comment.send_now` | member | yes | `memo`, `agent`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.memo.delete` | agent | yes | `task`, `memo`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.view.list` | member |  | none | `{views}` |  |  |
| `task.view.save` | member | yes | `name`, `filter`, `request_id` | `Accepted` | `bad_request` |  |
| `task.view.delete` | member | yes | `name`, `request_id` | `Accepted` | `not_found`, `bad_request` |  |
| `task.queue` | agent | yes | `tasks`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.draft` | agent | yes | `tasks`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.merge` | agent | yes | `into`, `tasks`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.release` | agent | yes | `task`, `to?`, `reason?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.request` | agent | yes | `request`, `title?`, `owner?`, `request_id` | `Accepted & {task, existing}` | `refused`, `not_found`, `bad_request` |  |
| `task.none` | agent | yes | `request`, `reason`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.wait` | agent | yes | `task`, `watch`, `time_box?`, `request_id` | `Accepted & {gate}` | `refused`, `not_found`, `bad_request` |  |
| `task.discard` | agent | yes | `task?`, `tasks?`, `reason?`, `reasons?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.board` | agent |  | `everyone?` | `{mine, reports}` |  |  |
| `task.withdraw` | agent | yes | `task`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.cancel` | member | yes | `task`, `reason?`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.edit` | agent | yes | `task`, `owner?`, `reporter?`, `title?`, `body?`, `time_box?`, `priority?`, `estimate?`, `blocked_by?`, `unblock?`, `add_tags?`, `remove_tags?`, `request_id`, `pr?` | `Accepted` | `refused`, `not_found` |  |
| `task.ask` | member | yes | `task`, `to`, `text`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |

### `tasks.list`

Task rows. Newest id first. To read every task, call with `all: true` and `limit: 200`, then again with `before` set to the smallest `id` of the page before, until `more` is false: nothing is skipped or repeated, because a page is the rows with an id below `before`. The caller's own scope still applies (itself and its reports for an agent, unless `everyone`).

### `task.get`

One task with body, delivery history and linked events.

### `task.history`

Every change to a task, newest first: one row per field changed, with who made it, the value before and after, and when. It is read from the events each task write keeps in its own transaction, so it is never edited or deleted; a fix is a later row. A deleted memo's words stay in it. Anyone may edit a task (task.edit); this history is what makes that safe to undo by hand.

### `task.time`

Where a task's time went, from its opening to its end (or now): segments in order that cover it with no gap or overlap. Read from what the daemon already records (the task's state events, its blockers, its asks of the founder, its holder's parks and starts); nothing is timed twice. Where two causes overlap, the stretch shows one, in this order: waiting on the founder, blocked by a task, waiting on another agent, queued, at an account's wall, starting, working.

### `task.shot`

A screenshot kept with a finished task. One screenshot of a task whose end recorded it, in chunks. A task or number the result does not hold is not_found.

### `task.new`

Create a task. The founder or a member device opens one for an agent only, and it opens queued. The owner may not be the caller: a task an agent takes on itself is begun from the founder's message (task.begin).

### `task.handoff`

Hand a task to another agent. Anyone may hand an open task on, not only its owner (the asker takes a task back so). A review is a handoff: the holder hands the task to its reviewer, who hands it back with findings or on, or marks it done. task.history's row names who made the move (by). A handoff to `human` will be refused (gate state: keep the task and ask with `@human` in a comment, task.comment.add); that refusal is switched on later, once the founder's open tasks are migrated, and until then it is accepted as before.

### `task.done`

Finish a task with evidence. Exactly one of evidence, request or reason-with-evidence; none or two is bad_request. A commit and shots are optional, for any task; when given they are kept with the task (task.get result). A done by the owner or the founder closes the task: nothing waits on an accept. The founder's done with evidence words on a task they own whose card still waits on them decides that card with the words, so the asker is told and what waits on the answer (a trust proposal) is applied; it says `T<n> decided`.

### `task.reopen`

Reopen a done task: anyone may, with a reason the opener and assignee are told. A discarded task comes back as a draft, to be queued again.

### `task.progress`

Record progress (hl progress): on a task, or with no task on the caller's request in progress, else the task it holds, else the agent, even when it holds no task.

### `task.begin`

Start a task the founder told the caller to do, without approval: opened for the caller, linked to the founder's message.

### `task.start`

Pick up a task as the caller's current one.

### `task.comment.list`

Comments beside a task. The wire still calls a comment a memo (Memo, `memo`, `memos`); task.memo.list is its old name.

### `task.memo.list`

The old name of task.comment.list: the same params, result and errors. Kept for clients that call it.

### `task.comment.add`

Add a comment; anyone may. It is told to whom the task answers (its reporter, else its opener), to its assignee whatever the task's state, and to each agent it names as @name; never to its writer. The founder is told by one inbox line per request (a task made from their message, task.request) while it stays unread, counting the agents' new comments. The holder of a doing task and a reader at its prompt are told at once; a reader mid-turn is told at the end of its turn, with all else then waiting for it in one message, and its lead is told if it is still unread an hour on. comment.edit and comment.delete are told the same way. A comment by an agent that names `@human` asks the founder, as the description says. The wire still calls a comment a memo (Memo, `memo`, `memos`); task.memo.add is its old name. A comment by an agent that names `@human` asks the founder: it opens one ask to the founder, linked to the task (InboxItem kind ask, `task` and `memo` set), so the task reads waiting on the founder. The ask stays open until (1) the founder replies to it (inbox.reply), (2) the founder adds any comment to that task, (3) the founder marks it read (inbox.mark_read naming its id), (4) its comment is deleted or edited so it no longer names `@human`, or (5) the task is done or discarded. An edit that keeps `@human` keeps the one ask and updates its body; one that adds `@human` opens it.

### `task.memo.add`

The old name of task.comment.add: the same params, result and errors. Kept for clients that call it.

### `task.comment.edit`

Edit a comment: its author or the founder. An edit that drops `@human` withdraws the ask it opened (task.comment.add); one that adds `@human` opens one. The wire still calls a comment a memo (Memo, `memo`, `memos`); task.memo.edit is its old name.

### `task.memo.edit`

The old name of task.comment.edit: the same params, result and errors. Kept for clients that call it.

### `task.comment.delete`

Delete a comment: its author or the founder. Deleting a comment withdraws the ask it opened (task.comment.add). The wire still calls a comment a memo (Memo, `memo`, `memos`); task.memo.delete is its old name.

### `task.comment.send_now`

Send a founder's comment now to a reader whose tell waits for the reader's next digest. Refused when the tell is not held (already sent, read or never told).

### `task.memo.delete`

The old name of task.comment.delete: the same params, result and errors. Kept for clients that call it.

### `task.view.list`

The saved views of the task board, by name.

### `task.view.save`

Keep a filter of the task board under a name, replacing a view already kept under it.

### `task.view.delete`

Delete a saved view.

### `task.queue`

Approve draft tasks to run: anyone may; each task's opener and assignee are told.

### `task.draft`

Send queued or doing tasks back to draft: anyone may; each task's opener and assignee are told. A holder working the task is told to stop and moved on to its next task, and commits or changes it left in its worktree are kept in a memo on the task.

### `task.merge`

Fold tasks into one: the kept task takes their titles, requests and sub-tasks; anyone may, and the opener and assignee of each are told.

### `task.release`

Take a doing task back to queued or draft: anyone may, with a reason the opener and assignee are told (and the founder, for a task begun on their message).

### `task.request`

Make a founder's chat message a request: a task the founder opened (opened_by human), owned by the lead the message was sent to. The message becomes its first comment, images included. A message converts once; a second call returns the task already made from it. The founder, or the agent the message was sent to, may call it. Clears a no-work mark on the message.

### `task.none`

Mark a founder's chat message as needing no work, with a reason in one line, so it is not chased as unanswered. Refused for a message already made a request. The founder, or the agent the message was sent to, may call it.

### `task.wait`

Make a task wait on outside state: a gate on watch (as task.new with owner gate), the task blocked by it and, when doing, released, all in one write, so the task never waits on a gate that blocks nothing. The task comes back to its holder's queue when the gate opens.

### `task.discard`

Throw away a task that is not done: anyone may, with a reason the opener, assignee and asker are told. A request still open on it refuses, unless the caller leads a team whose tree holds the task (its owner or opener is the lead or reports to it): then each open request is withdrawn and its target told. tasks in place of task discards a batch in one call, all or none, with one discard_batch event: the one task_batch card in the caller's chat. A request that already landed in its agent's session does not refuse the task's asker or the founder: the task is discarded, that request closes with it and its holder is told why; anyone else is refused.

### `task.board`

The caller's open tasks and its reports', and who holds each now. The founder's board is the tasks the founder owns.

### `task.withdraw`

Withdraw a task the caller asked for; anyone else discards it with a reason.

### `task.cancel`

Cancel a task.

### `task.edit`

Change a task: owner, reporter, title, priority, body and time box, by anyone (body and time box until it is delivered). Waiting on other tasks is added (blocked_by) or removed (unblock) with it. Never silent: the opener and assignee are told, and the edit is kept with who made it.

### `task.ask`

The founder asks an agent a question about a task: a message linked to the task, whose holder does not change. The agent's reply joins the task.

