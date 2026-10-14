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
2. Go: `tasks.list` `under`, `Task.waits`, `Task.waiting` (with the stored cause). The fake daemon follows each slice. Done: in `contract/protocol.json`.
3. Apps (handline.pc, phone): the image picker in the task body and memo editor, `task.reject` with the circled picture, and the graph on `under` + `waits` + `waiting`.
