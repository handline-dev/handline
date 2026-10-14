# Story 1: images in tasks, and the task graph (contract proposal)

Slice 1 of the daemon side of Story 1 (founder story "an agent Jira", frame in `docs/story1-research.md` on the story1-research branch). Contract only: what the build slices then serve. Nothing here is in `contract/protocol.json` yet, because no daemon serves it; each part below is written to be applied as it stands, as `docs/contract-requests.md` does. Every part only adds (a field, a param, a reference form), so protocol stays 1.

The founder's answers this follows: a picture goes in a comment or in the task body, and the body is Markdown (answer 1); Send back is `task.reject` with the circled picture (6); waiting reasons are one field of the task (7); an image may be uploaded from another device than the task or capture came from (direction C).

## A. Images in a task body, a memo and a reject note

**Reuse, no second upload.** `attachment.put` (chunked, roles member and founder) already answers an `Attachment` with an id, and `attachment.get` reads it or a thumbnail. An id belongs to the daemon, not to a device or a session: a phone may upload it and a PC write the task that names it, or the other way. Nothing ties an upload to where the task came from.

**How text names an image.** Markdown image syntax with the `attachment:` scheme:

```
![the login button, circled](attachment:9f2c41d0a7b3e6c8)
```

- Where: `task.new` `body`, `task.edit` `body`, `task.memo.add` / `task.memo.edit` `text`, and `task.reject` `note` (it stops being one line only when it carries images: one line of words, then the image references).
- The text is stored as written. The daemon reads references only of that exact form (`![` caption `](attachment:` id `)`); anything else is plain text.
- At most 6 images per text, as for a chat message.

**What the daemon checks**, in the write's own transaction:

| Check | Refusal |
|---|---|
| The id is well formed and its upload finished | `bad_request`, field `body` / `text` / `note`, reason names the id |
| The image is still kept (not swept) | `not_found`, what `attachment` |
| More than 6 references | `bad_request`, same field |
| The caller may read the image (below) | `refused`, gate `role` |

Who may name an image: a member or the founder, any kept id (they may `attachment.get` it today). An agent may not upload in v1, so an agent may name only an id already named by a task, memo or reject note it may read, which lets it carry a picture from a task into a child task or a handoff. An edit that keeps a reference already in the text is never refused for it.

**Keeping.** A write that names an image claims it, as a chat message does (`Claim`: kept 30 days from the claim). An image named by an open task (its body, a memo or a reject note) is not swept; it is kept 30 days after that task is done or discarded. A reference whose image is gone stays in the text; the part (below) says `gone`.

**What a client reads, without parsing.** `TaskDetail` (task.get) gains `body_parts`, `Memo` gains `parts`, and a `TaskDetail.timeline` row gains `parts` when its text names an image (a memo, an edited brief, and a reject, which becomes a row of its own: today its note is kept only in the event and the line typed to the owner). Each is the text cut at its references, in order:

```jsonc
"body_parts": [
  {"text": "The button is cut off on a small window:\n"},
  {"image": {"id": "9f2c41d0a7b3e6c8", "name": "login.png", "mime": "image/png", "bytes": 184320}, "caption": "the login button, circled"},
  {"text": "\nMake it wrap."}
]
```

- An image part is an `Attachment` plus `caption`; `gone: true` (and no `mime`/`bytes`) when the image is no longer kept, so the client shows the caption without a picture.
- The parts joined give back the text with each reference replaced by nothing, so a client that cannot draw images shows the text parts and a list.
- Present only when the text has at least one reference; `body` and `text` stay as they are, so today's clients do not change.
- The cut is the daemon's, which avoids offsets (Go counts bytes, a JavaScript string UTF-16 units).

**What an agent gets.** An agent cannot `attachment.get`; it reads the file by path, as for chat (`x-attachments` reach). Wherever the daemon types a task's brief, a memo or a reject note to an agent, it adds one line per image after the text, the chat form: `[image N: /absolute/path.png (image/png, 184 KB)]`. For an agent caller only, an image part of `task.get` also carries `path`, so `hl task show` can print the same lines. A member or founder client never gets a path.

**Who may `attachment.get` an image named from a task.** Anyone who may read the task. In v1 that changes nothing: members and the founder read every task and may get any image, and agents read by path. The rule is written down now so that a narrower device role later (a remote machine) is checked against the task or message that names the image, not against who uploaded it.

**Contract edits.** `x-attachments`: add "Tasks" (the reference form, the checks, keeping, parts, the agent's path lines) and change "roles" to name the agent's reference rule. Params: the descriptions of the five texts above. Schemas: `ImagePart` (`Attachment` and `caption`, optional `gone`, `path` for an agent), `TextPart` (`text`), `TaskDetail.body_parts`, `Memo.parts`, the timeline row's `parts`, and the timeline kind `reject` (each parts field an optional `array` of `oneOf` `TextPart`, `ImagePart`). Fixtures: a task with two references read back in parts; an unknown id refused; an agent naming an id it has not read refused, then accepted for one in its parent's body; a memo and a reject note with an image; a swept image read back as `gone`.

## A+. Chat uses the same path

The founder's order: one image path for three places, chat messages, task bodies (now Markdown) and memos. Part A is that path; this part brings chat onto it. Upload stays `attachment.put`.

**The texts.** The reference form, the checks, keeping, parts and path lines of part A apply to every text a person or an agent sends as a message too: `chat.send` `body`, `say` `body`, `inbox.reply` `text` and `task.handoff` `note`. One parser, one checker and one keeper serve all of them; only the field named in a refusal differs.

**`chat.send` `attachments` stays.** Today's apps send a list of ids and keep working. The two forms meet in one message:

- Its images are the references in the body, in order, then the listed ids not already referenced. An id in both counts once; at most 6 in all (`bad_request`, field `attachments` when the list is what goes over, else `body`).
- `ChatMessage.attachments` keeps listing every image of the message, in that order, so today's clients draw them all. Such a client shows a reference's text as written; a client that reads `parts` does not.
- The checks are part A's for both forms, in the write's own transaction; the count of 6 is checked first. Two changes for the list, so both forms answer alike: an id already sent is accepted (the fake daemon refused it, the Go daemon did not), and a well-formed id never put or no longer kept is `not_found`, what `attachment`, as the Go daemon already answered (the contract said `bad_request`).
- With `task` the body is the new task's brief, so references in it are allowed and checked as `task.new` `body`. The list stays refused with `task`: a brief names its images in its text.

**Who may name an image in a message.** As for a task: a member or the founder any kept image; an agent only one a task's text or a message already names or was sent with (`attachments` counts), so it can pass on a picture it was shown, never an upload no one has sent.

**Keeping.** A message that names an image claims it, kept 30 days after the message, as today. A handoff note is a text of its task, as a reject note is, so its images are kept while the task is open and 30 days after. An image is removed only when nothing keeps it any more: no open task names it, and its last claim and last task are 30 days old.

**What a client reads.** `ChatMessage` and the `request.read` result gain `parts` with part A's shape (`TextPart`, `ImagePart`), present when the message has an image: the body cut at its references, then one image part per listed id, with an empty `caption`. `ChatMessage.attachments` and `turn_start.attachments` list every image of the message. A timeline row of kind `handoff` gains `parts` as a memo does. `body` stays as it is.

**What an agent gets.** Delivery adds one line per image after the text, the form it has today (`[image N: /absolute/path.png (image/png, 184 KB)]`), numbered over the message's images in the order above; for a task's request the images its memos name follow, numbered on. `request.read` gains the same `parts`, so `hl read` prints the same lines, which it does not today. An `ImagePart` carries `path` for an agent caller only.

**Markdown.** A task body, a memo and a message are stored as written. The daemon reads only the image references, anywhere in the text (inside a code span too); the rest is the client's to draw. A client draws the text as Markdown (CommonMark with GFM lists and tables), shows raw HTML as text, and never fetches an image by another scheme (an `http:` image is shown as a link), so nothing outside the daemon is loaded. It draws images from `parts`, not by parsing the text. A client that does not draw Markdown yet shows the text parts as plain text, as today. Titles stay plain text. An agent reads the Markdown as written.

**Contract edits.** `x-attachments`: "what" and "flow" name messages and tasks as one path; "tasks" becomes "texts" listing the nine fields; "roles" covers messages; "storage" gets the keeping rule above. `chat.send` `attachments`: how it meets the body's references, and the two changed refusals. The descriptions of `say` `body`, `inbox.reply` `text`, `task.handoff` `note`, and `Task`/`TaskDetail` `body` (Markdown). Schemas: `ChatMessage.parts`, `request.read` `parts`, `parts` on the `handoff` timeline row. Fixtures (Go conformance, as the founder): a `chat.send` with a reference and a listed id, the same id in both counted once, read back in `parts` and `attachments`; an already sent id accepted; 4 referenced and 3 listed refused; a reference to an image never put `not_found`. The Go tests cover what the binary's run cannot: `chat.send` with `task` (it has no task teller), an agent's `say` and handoff note (it has no agent connection).

## B. The task graph (1-6)

**Is `tasks.list` enough?** Mostly yes, and no `tasks.graph` method is proposed. A `Task` row already carries a node and its edges:

| Graph needs | Already in `Task` |
|---|---|
| node id, title, state | `id`, `title`, `state` |
| assignee | `to` |
| parent edge | `parent` |
| blocked-by edge | `blocked_by` (open blockers only) |
| waiting reason | `hold_reason`, `block_reason` (text), and `TaskBoardRow.now` on the board |

Three things are missing, each a small addition:

1. **One story in one call.** `parent` filters direct children only, so a story of three levels takes a call per level. Add `tasks.list` param `under` (task id): that task and every task below it, any depth, done and discarded included (`all` is implied), still within the caller's scope, newest first and paged as today. 1-6 reads a story with `under`, or the caller's whole board with today's params.
2. **Waits that are over.** `blocked_by` drops a blocker once it closes, but the graph draws a done blocker too, as a ticked node with its arrow (story1-research, "The graph on PC"). A done blocker's wait stays recorded (`task_blocks`; a discarded blocker's wait is dropped with it), so add `Task.waits`: every task this one has waited on, `[{task, open}]`, oldest first; `blocked_by` stays as it is.
3. **The waiting reason as one field.** Answer 7. Today it is free text in two fields plus the board's `now`. Add `Task.waiting`, `null` when nothing waits, else:

   ```jsonc
   "waiting": {"on": "task", "text": "blocked by T<n>", "tasks": [<n>], "since": 1792000000}
   ```

   `on` is one of `founder` (`waiting on you`, an open request to the founder on the task), `task` (`blocked by T<n>`, from `blocked_by` and open children), `cap` (`at its cap`), `wall` (the account's usage or plan limit), `host` (`host full: n of H`, Story 2; not sent until the host cap exists), `owner` (the owner is busy: mid-turn, a dialog, closed, another task ahead). `text` is the line every view shows the same way (the graph edge label, the time bar, the task page). Today's hold record keeps only free text, so the build slice stores the cause as one of these words next to it. `hold_reason` and `block_reason` stay until the apps read `waiting`.

**Scope** is the caller's, as for every task read: an agent sees itself and its reports, a member and the founder everything (with `everyone`). A blocker outside the caller's scope still appears in `waits` and `waiting.tasks` by id, without its row.

**Fixtures** (`contract/fixtures/task_graph.json`). `tasks.list` with `under` on a three-level story (every level, a done child included, and a task outside the story left out); `waits` with one open and one done blocker; `waiting` for a task behind a blocker (`task`) and one waiting on a founder's answer (`founder`). `cap`, `wall` and `owner` come from the supervisor's sweep and are covered by Go tests.

## Build slices that follow

1. Go: `body_parts` and the `parts` fields, the reject row of the timeline, the reference checks, keeping, path lines to agents. hl prints the path lines in `hl task show`.
2. Go: part A+, on part A's parser, checker and keeper; path lines in the inbox file.
3. Go: `tasks.list` `under`, `Task.waits`, `Task.waiting` (with the stored cause). The fake daemon follows each slice. Done: in `contract/protocol.json`.
4. Apps (handline.pc, phone): the image picker in the task body and memo editor, `task.reject` with the circled picture, and the graph on `under` + `waits` + `waiting`.
