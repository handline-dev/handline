<!-- Generated from contract/spec/ by contract/generate.py; do not edit. -->
# chat methods (protocol 1)

A method marked write takes a required `request_id`. Framing, roles, errors and gates are in [protocol.md](../protocol.md); field types in `protocol.ts`.

| Method | Role | Write | Params | Result | Errors | Gates |
|---|---|---|---|---|---|---|
| `chat.messages` | member |  | `agent`, `scope`, `before?`, `since?`, `limit?` | `{messages, more} & ReadCursor` | `not_found`, `bad_request` |  |
| `chat.requests` | member |  | `agent`, `scope`, `before?`, `since?`, `limit?` | `{items, more, version, cursor}` | `not_found`, `bad_request` |  |
| `chat.transcript` | member |  | `agent`, `before?`, `since?`, `limit?` | `{items, more, working} & ReadCursor` | `not_found`, `bad_request` |  |
| `transcript.events` | member |  | `agent`, `since_seq?`, `before_seq?`, `limit?` | `TranscriptEvents` | `not_found`, `bad_request`, `refused` | unsupported |
| `chat.locate` | member |  | `request`, `agent?`, `limit?` | `{agent, scope, message, messages, more_older, more_newer, transcript, transcript_more_older, transcript_more_newer}` | `not_found`, `bad_request` |  |
| `chat.refs` | member |  | `agent` | `{requests}` | `not_found`, `bad_request` |  |
| `chat.search` | agent |  | `agent?`, `query`, `scope?`, `with?`, `since?`, `limit?` | `{messages}` | `bad_request`, `not_found`, `refused` | role |
| `chat.send` | member | yes | `agent`, `body`, `task?`, `parent?`, `quote?`, `attachments?`, `parent_item?`, `delivery?`, `request_id` | `Accepted & {request, receipt}` | `refused`, `not_found`, `bad_request` |  |
| `chat.send_now` | member | yes | `request`, `request_id` | `Accepted & {receipt}` | `refused`, `not_found`, `bad_request` |  |
| `chat.cancel` | member | yes | `request`, `confirm?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `chat.seen` | member | yes | `agent`, `up_to`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `attachment.put` | member | yes | `name`, `mime`, `bytes`, `offset?`, `upload?`, `data`, `request_id` | `Accepted & {upload, received, attachment}` | `refused`, `not_found`, `bad_request` | role, limit |
| `attachment.get` | member |  | `id`, `thumb?`, `offset?` | `{mime, bytes, offset, data, done}` | `refused`, `not_found`, `bad_request` | role |
| `inbox.list` | agent |  | `unread?`, `kind?`, `agent?`, `before?`, `limit?`, `wait_s?` | `{items, more} & ReadCursor` |  |  |
| `inbox.queue` | agent |  | `whose?`, `all?`, `since?`, `wait_s?` | `{whose, newest, head, history, waiting, lines, dealt}` | `refused`, `bad_request` | listing |
| `inbox.get` | agent |  | `id`, `wait_s?` | `InboxItem & ReadCursor` | `not_found`, `bad_request` |  |
| `request.read` | agent |  | `id`, `wait_s?` | `{id, ref, requester, target, kind, state, created, chain_depth, parent, body, answer, parts}` | `not_found`, `bad_request` |  |
| `request.withdraw` | agent | yes | `id`, `reason?`, `request_id` | `{ok, said}` | `bad_request` |  |
| `request.cancel` | founder | yes | `id`, `dry_run?`, `request_id` | `{ok, plan, said}` | `refused`, `bad_request` |  |
| `inbox.mark_read` | agent | yes | `ids`, `request_id` | `Accepted` | `not_found`, `refused`, `bad_request` |  |
| `inbox.reply` | agent | yes | `id`, `text`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `requests.list` | member |  | `state?`, `before?`, `limit?` | `{items, more}` | `bad_request` |  |
| `answers.list` | founder |  | none | `{answers}` |  |  |
| `answer.add` | founder | yes | `folder?`, `vendor?`, `title?`, `label?`, `request_id` | `Accepted` | `refused`, `bad_request` | unsupported |
| `answer.remove` | founder | yes | `kind`, `key`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |

### `chat.messages`

Messages of an agent's chat, newest first. Page with before (older) or since (newer).

### `chat.requests`

A chat's threads for the screen that draws them: the requests of an agent's chat oldest first, with the task changes and the session starts among them, and one version that changes when anything in the chat does. items are oldest first, each with a `type`: `request` is a chat.messages ChatMessage plus `task_state`; `task_event` is a change of a task delivered in the page, since the first request in it (the state it reached, a handoff, an edit), with `request` naming the delivery a handoff belongs to so a client can draw both as one card; `session` is the start of a new conversation of the agent since that request (one that resumed the old conversation is not listed). Requests that no chat draws (withdrawn, never reached their target) are left out. HR cards are hr.list's.

### `chat.transcript`

The agent's replies, tool activity, prompt cards and task events as ordered items, plus its working state. Oldest first, in pages: `before` reads the page just older than an item id, `since` reads the items from the second that id is in. Items are the agent's replies, its tool calls, the prompts it opened and the events of the tasks it owns. A tool item's id is the second of the call and its `output` is what the store held when the page was read: a result that lands in a later second than its call is not sent again by `since`, so a client that needs it reads the page again (without `since`). A page carries at most 2 MiB of tool output between its items; the newest items keep their output and an older one is cut to fit, with `dropped_lines` counting what was left out. Session items (kind session) are part of the list: each carries session.cause and session.handoff, and sorts among the others by its second.

### `transcript.events`

A page of the agent's transcript events, by seq: the snapshot of the transcript topic. Refused (gate unsupported) for an agent the daemon does not run as a structured session (a terminal agent: watch its terminal). since_seq with before_seq, or a limit outside 1 to 500, is bad_request naming the field. Served for a structured agent whether or not its session is live, from the stored log.

### `chat.locate`

Where a message sits (its msg_ name, a bare hash or its number): its conversation, scope, the page of messages around it and the transcript of the same span. not_found when the request does not exist or no chat draws it (withdrawn, or never reached its target). The page has up to `limit` messages, about half of them older than the one asked for. When the request exists and only no chat draws it, the not_found data also carries `from`, `to` and `body` of that request (body as typed, control characters stripped), so a client can say what it was; for a request that does not exist it carries none of them.

### `chat.refs`

What a # in the composer can point at: the agent's newest requests. The agent's tasks are `tasks.list` with `owner` or `from`. A request that no chat shows is read with `chat.locate`, whose not_found carries from, to and body.

### `chat.search`

Find messages by text; each hit carries the scope that shows it (the jump is chat.locate). An agent searches only its own messages: `agent` absent is the caller, any other name is refused (gate role).

### `chat.send`

A message to an agent through the daemon's delivery (queued, typed, landed); needs no input hold. With parent, a reply to a message. To a structured agent the request_id is the operation id and the result carries a receipt. To a structured agent the result holds `receipt`; to a terminal agent it does not. A repeat (the same request_id and params) returns replayed true and the receipt as it is now, not the first reply, and writes nothing; see x-delivery. A repeat of an operation that was withdrawn is refused (gate state): a retry does not bring back what the person took back. With attachments, the delivered text ends with one path line per image (x-attachments). With parent_item the delivered text begins with the reply quoted, each line after `> `, then a blank line, then the body. The quote is the reply's first 300 characters, counted as Unicode code points (not UTF-16 units), with no ellipsis added; the daemon cuts, once, and keeps the same text with the message (ChatMessage.parent_item), so a client draws it without reading the transcript again and the stored and the delivered quote are the same.

### `chat.send_now`

Write a queued message now, ahead of the daemon's delivery order; the request keeps its id and goes on as sent. For a message to a structured agent whose receipt is queued: written to the CLI with priority now, receipt sent. Any other receipt is refused (gate state), including a message written meanwhile. The daemon makes the release atomic with its queue, so a write that has started is never promoted twice. A terminal agent's message is as before.

### `chat.cancel`

Take back a message the person sent, delivered or not. The person's own undo. A message not yet delivered is taken back and nobody is told. To a structured agent a message whose receipt is queued is withdrawn the same way, with no confirm: it was never written to the CLI. A delivered one (receipt sent, confirmed or unknown, or a terminal agent's delivered message) is closed as rejected after confirm, its agent is told to stop and whatever queued behind it moves up. A withdrawn operation is dropped from chat.messages and a chat.send of its request_id is refused (gate state). A message the person did not send, or one already closed, is refused (gate state).

### `chat.seen`

Record that the person has an agent's messages in front of them, up to a message number. What it moves: the agent's messages to the person up to up_to leave the FOR YOU list as new (the mailbox's unseen count). Only that list forgets; decisions still count in the summary. An ask raised by a task comment stays open past up_to (task.comment.add says what closes it). A client sends the newest of the agent's messages that has been in view, never merely loaded, so opening a chat clears only what it showed.

### `attachment.put`

Upload a file for a message, in chunks; the last chunk returns the Attachment to name in chat.send. See x-attachments. A chunk whose offset is not `received` is bad_request, field offset, and the reply's data says what the daemon holds, so a client that lost a reply resumes from there; the same request_id and params replays as for every write and writes nothing. When received reaches bytes the daemon checks an image type (PNG, JPEG, GIF, WebP) by the first bytes, takes any other file as sent, and answers with `attachment`; a size over 10 MiB, an unsent total over its limit or too many open uploads is refused, gate limit, and the partial upload is dropped. An upload id that is unknown or expired is not_found.

### `attachment.get`

Read an attached image or a memo's file, or a small copy of an image, for drawing. See x-attachments. An id that no longer exists (never put, or kept past its time) is not_found.

### `inbox.list`

Inbox items; an agent sees its own.

### `inbox.queue`

An inbox as hl inbox prints it. An agent's inbox is the one request at the head of its queue (the oldest queued one whose routing was checked; a say is never the head), and with `all` every request ever sent to it. The founder's is the mailbox folded to one line per agent, a decision keeping its own line, and with `all` the 20 most recent requests to or from the founder that are no longer queued. An agent may list its own inbox and those of the agents it supervises; the founder any. Nothing is marked read: the caller sends inbox.mark_read for the head it showed.

### `inbox.get`

One item.

### `request.read`

One request whole, as hl read prints it. Any caller may read any request, with both of its ends named; a request to an agent that the routing table has not passed yet is not_found to that agent. The founder reading one opens it: it is marked read, and when it is one the founder asked, its answer is marked told. A member's device reading it opens nothing. With `wait_s` the call holds while the request is queued; `state` says whether it settled, and a wait that ends with it still queued opens nothing.

### `request.withdraw`

Take back a request of your own before it is delivered, as hl withdraw does. The caller is the requester: an agent's session withdraws that agent's request, and the founder's key withdraws the founder's own. A request that is not the caller's, one already delivered or closed, and an id that does not exist are not errors: the reply has `ok` false and `said` gives the reason.

### `request.cancel`

The founder takes any request out of an agent's queue, as hl cancel does; dry_run names what would happen. Unlike chat.cancel it is not limited to messages the person sent, and it asks for no confirm: the call without `dry_run` is the go-ahead. A queued request is closed as rejected, delivered or not; its agent is told to stop when it was delivered, and its requester is told unless that is the founder. The founder's own request that was never delivered is withdrawn and nobody is told. A request that is gone or already closed is not an error: `ok` is false, with the reason in `plan` (dry run) or `said`. A dry run writes nothing and records nothing, and no reply is kept under the request_id.

### `inbox.mark_read`

Mark items read; an id that is not yours is not_found. For the founder, every message of an agent up to the newest id named is read; a decision or a reply owed is never read away, so such an id, and what lies past it, stays unread (refused, gate state, when nothing could be read). For an agent only the head of its own queue can be read, as `hl inbox` does; another id of its own is refused, gate state. An ask raised by a task comment (InboxItem.memo) is read only by naming its own id: reading an agent's messages past it leaves it open.

### `inbox.reply`

Reply to an item. A message to whoever sent the item, with the item as its parent link (`hl say --parent`); it settles nothing. An agent replies to its own incoming items; the founder to a mailbox item that is not a decision (a decision is a task, closed with task.done: refused, gate state). A reply to an ask raised by a task comment (InboxItem.memo) is also added to that task as the founder's comment, a reply to that comment, which closes the ask and is told to the task's readers at once.

### `requests.list`

The founder's requests, newest activity first: the tasks they opened and the messages of theirs that are not a task yet, each with a status read from its tree of tasks. Done requests stay listed 30 days.

### `answers.list`

The founder's saved answers: trusted folders and dialog rules (Home settings). A daemon that answers no dialog lists no dialog rules (x-dialogs).

### `answer.add`

Save a standing answer: a folder to trust, or a dialog rule (vendor, title and label). Blank fields add nothing; a half-filled rule is refused. Founder because the dialog-rule store and trust are the founder's alone: an agent that could write them could answer its own dialogs. A dialog rule (vendor, title, label) is refused, gate unsupported, and nothing is saved, because the daemon answers no dialog (x-dialogs); a folder is saved.

### `answer.remove`

Remove a standing answer by the kind and key answers.list gave.

