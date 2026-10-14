# Story 1: research and one UX frame

Founder story T3846, "an agent Jira". This is 1-0: what similar tools do, what we borrow and under which licence, and one frame in which 1-1 to 1-6 are one flow. Nothing is built until the founder agrees the frame (§5).

Sources are each tool's own README or docs where the search found them, and third-party write-ups where it did not; a row says which. Licences are from the GitHub repository's licence field (2026-10-14). Closed tools give ideas only. AGPL code is not borrowed at all: a network service built on it would have to publish its source.

## 1. What the tools do

| Topic | Tool | How it does it | We borrow | Licence |
|---|---|---|---|---|
| **Capture and annotate into an issue (1-1)** | Orca | Design Mode: click an element in an embedded Chromium, its HTML, CSS and a cropped screenshot go into the agent's prompt | The picture goes in with the words as one message; the crop is of what the person pointed at | MIT (code may be reused) |
| | Cursor 3 | Design Mode in the agents window: click an element or draw on the page, say the change, the agent edits while you go on; a forum report says the crop is off on a scaled second display | Draw on the picture, then say it; test the capture on a scaled display | closed (idea) |
| | Marker.io, Jam | A browser widget bundles an annotated screenshot with URL, browser, screen size and console log, and creates the issue in Jira, GitHub and others (third-party write-ups) | The issue carries the context it was taken in: which window, size, which build | closed (idea) |
| **Review inside the task (1-2)** | Vibe Kanban | Per its docs, the task moves to In Review when the agent stops; line comments on the diff are submitted together (the move back to In Progress is our assumption, not found in its docs) | Review is a state of the task, and sending back carries the comments with it | Apache-2.0 (code may be reused, keep the notice) |
| | Orca | Comments on any diff line are shipped back to the agent | The same: a comment is a message to the agent | MIT |
| | Conductor | Per-workspace diff to review before the PR; sources disagree on checkpoints, not in its own docs | The diff stays out of main until the person says yes | closed (idea) |
| | Claude Squad | A tmux session per agent, review by attaching | Nothing: no review model | AGPL-3.0 (no code) |
| **Tags and filters (1-3)** | Vibe Kanban | In its cloud board, tags are labels to categorise and filter tasks (its Tags page describes text snippets instead) | Free labels, filter by one or several | Apache-2.0 |
| | Linear, Jira | Labels on an issue, saved filters and views | A filter is saved and named, not typed again | closed (idea) |
| | Plane | Open-source Jira alternative: labels, views | Ideas only | AGPL-3.0 (no code) |
| **Edit history (1-4)** | Jira (history tab, StatusPath-style apps) | Each change as who, field, old value, new value, when; compare past and current | The same four columns, newest first, one row per field | closed (idea) |
| | Linear | The activity feed under an issue (the search found no official page: checked by use, not by source) | History is under the task, not on another screen | closed (idea) |
| **Time in status (1-5)** | Jira Time in Status apps (Tempo, Time and History) | Elapsed time between status changes from the change history, with working hours; a horizontal bar with one block per status sized to real time | One bar, a block per status, sized by time; the history is the source, nothing is timed twice | closed (idea) |
| | Linear (through Screenful, Jellyfish) | Cycle and lead time by status per issue, trend over completed items | Estimate beside actual, and a trend over finished tasks | closed (idea) |
| **Graph or canvas (1-6)** | React Flow (xyflow) | Node-based UI library: pan, zoom, custom nodes, edges, minimap | The canvas itself | MIT (use as a dependency) |
| | Excalidraw | Whiteboard library, hand-drawn style | Its drawing tools, if the annotation of 1-1 outgrows our own | MIT |
| | tldraw | Infinite canvas SDK | Nothing: the licence field is non-standard (licence key, watermark) | NOASSERTION (not used) |
| | Jira Advanced Roadmaps, Linear project timeline | Bars on a time axis, dependency arrows | The arrow means "waits on"; time runs one way | closed (idea) |

Positioning check (founder asked, 2026-10-14, two web searches): no tool found that does the whole difference. Closest on each part: Codex agents dashboard groups tasks as Need input, Working, Ready (attention, but no reason field and no load limit); Agent Task Board has Queued, Running, Review, Done lanes with a live timer (time in status, no reason it waits); Paperclip flags stale tasks, and a block is read from comments; Codex caps threads at a fixed 6 and OpenHands runs tools sequentially by default. Nobody found shows one waiting reason on every item, nor a cap that follows measured load. The search was shallow: a hit on a closed tool would not show here.

What the table says in a line: nobody does all six together. The agent IDEs (Orca, Conductor, Vibe Kanban) have capture and diff review but no history, time or graph; Jira and Linear have history, time and graph but no capture-to-agent and no "is it ok?" step. Our frame joins them because the agent is a person on the team and the task is where its work is told.

## 2. What we already have

| Piece | Today | Where |
|---|---|---|
| Image attach in chat | `attachment.put` then `attachments` on `chat.send` (PNG, JPEG, GIF, WebP) | web `ChatAttach.tsx`; not yet on the phone |
| Task states with a reported step | `reported` waits for `task.accept` or `task.reject` (note), `task.reopen`; the Go daemon serves them; the fake gets them in branch fake-task-accept (T3856), not yet merged | contract, Go daemon |
| Time | `TaskClock` (time box, start, end, overdue since, overdue notices); `deliveries` on `task.get` | `TaskTime.tsx` |
| History in part | `assignments` (who changed the assignee or owner), `memos` | `TaskDetail` |
| Links | `parent`, `blocked_by`, `subtasks` | `Task` |
| Tags | none | |
| Native window capture | none in `apps/electron` | |

## 3. The frame: chat is the way in, the task is where it lives

```
  see a problem ──▶ capture + circle + say it ──▶ becomes a task (spec = the picture + the words)
                                                      │
                  ┌───────────────────────────────────┘
                  ▼
      agent works ──▶ "I changed it like this, ok?"  (before / after, in the task)
                  │            │
                  │      approve ──▶ done          send back (circle again) ──▶ agent works
                  ▼
      tags · history · time bar · graph all read the same task
```

One rule: **a thing the person says about the work lands on a task as a comment, and a comment can carry a picture.** Chat stays what it is; "make this a task" is one action on a message or a capture. Each of 1-1 to 1-6 is a view or a verb of the task, not a new place.

### One picture of what waits and why

Handline schedules three scarce things: the founder's attention, agent accounts (plan and usage limits), and the machine (CPU, memory, disk). Work starts only when its resources are free, and the reason it waits is always shown, in one vocabulary:

| Waiting on | Shown as | Source |
|---|---|---|
| The founder | `waiting on you` | open human request |
| Another task | `blocked by T<n>` | `blocked_by` |
| An account limit | `at its cap` | account cap |
| An account wall | `wall` | the account's plan or usage limit is reached (Story 2) |
| The machine | `host full: n of H` | host cap (Story 2, `docs/story2-load-research.md`) |

The same reason text is the edge label in the graph (1-6) and the grey stretch in the time bar (1-5), and the task page shows it as one line under the title. The machine side comes from Story 2 (2-1 limits, 2-2 admin view); this frame only fixes that the reason is a field of the task, not a screen of its own.

### Where each lives

| Unit | PC app | Phone | Daemon work |
|---|---|---|---|
| 1-1 capture, circle, send | A Capture button in the title bar and ⌘⇧C: pick a window (the app, the simulator), draw on it (pen, arrow, box, text), write the order, send. It opens as a new task, or as a comment on the open one. The shot is the task's spec | Attach a screenshot (the picker is a native package, so a rebuilt dev client) and circle it in the same editor; the phone cannot capture the simulator, so it comments on a task | A comment of a task that carries attachments (memo with `attachments`); `attachment.put` exists |
| 1-2 review in the task | A Review card at the top of the task when it is `reported`: before and after pictures, the diff stat, Approve and Send back (circle and note) | The same card; Approve and Send back call `task.accept` and `task.reject` | Report carries before/after attachments; `task.accept`, `task.reject` exist |
| 1-3 tags and filters | Chips on a row, a tag field in the task, a filter in the Tasks bar that saves a named view | A filter sheet; tags show as chips | `tags` on a task, a filter on `tasks.list`, saved views |
| 1-4 history | A History tab on the task: who, field, was, now, when; a fix is a new row, not an erase | The same list under the task | An append-only `task.history`; every edit writes rows |
| 1-5 where the time went | One segmented bar under the title: waiting on a person, working, agent started or restarted; the estimate is a tick on it; an overrun shows the agent's own review as text | A thin bar in the task row; the detail on tap | The segments come from state events and deliveries; the self-review is an agent message at the overrun |
| 1-6 canvas | A Graph page beside the Tasks list: nodes are tasks, an arrow means waits on; top = done and mine-but-waiting, bottom = planned and blocked on me; pan and zoom; click opens the task | A read-only list sorted the same way (a canvas on a phone is not worth the build) | `blocked_by` and `state` exist; a single `tasks.graph` read avoids N task reads |

### Chat to task

```
  Chat with handline.architect
  ┌────────────────────────────────────────────────────────┐
  │ you:   the header overlaps the title on the phone      │
  │        [shot: header, circled]                          │
  │                                       [ Make a task ▸ ] │
  └────────────────────────────────────────────────────────┘
        │ one click
        ▼
  New task  T4101   to: handline.pc        tags: [fe] [phone]
  ┌────────────────────────────────────────────────────────┐
  │ spec    the header overlaps the title on the phone     │
  │         [shot: header, circled]  (from chat #812)       │
  │ box     1h          owner you                           │
  └────────────────────────────────────────────────────────┘
```

### The task page on PC

```
  T4101  Fix the header overlap                 [fe] [phone]   doing · handline.pc
  ─────────────────────────────────────────────────────────────────────────────
  time  [░░ wait you ░░][███ working ███][▒ restart ▒]      est 1h │ 1h20 used
  ─────────────────────────────────────────────────────────────────────────────
  Spec   Review ●   History   Messages
  ┌ Review: handline.pc says "changed like this, ok?" ─────────────────────────┐
  │   before                 after                    diff  +12 −4  2 files    │
  │  [ shot ]               [ shot ]                                          │
  │                         [ Approve ]   [ Send back (circle, note) ]         │
  └────────────────────────────────────────────────────────────────────────────┘
```

### The graph on PC

```
   done / mine, waiting on other tasks      ← top
  ┌─────────┐   ┌─────────┐
  │ T4090 ✓ │──▶│ T4101 ⏳ │──┐
  └─────────┘   └─────────┘  │
                              ▼
  ┌─────────┐   ┌─────────┐ ┌─────────┐
  │ T4120   │   │ T4125 ● │ │ T4130   │       planned / blocked on me   ← bottom
  └─────────┘   └─────────┘ └─────────┘
```

### The capture sheet on PC

```
  Capture ── window: [ handline web ▾ ]            [ pen ][ arrow ][ box ][ text ]
  ┌──────────────────────────────────────────────┐
  │   (the window as it was, circled in red)     │
  └──────────────────────────────────────────────┘
  What should change?  [ the header overlaps the title                     ]
  To: [ new task ▾ ]  tags: [fe]                            [ Cancel ] [ Send ]
```

## 4. Build order and what blocks what

1. **1-1 PC capture** can start alone: Electron window capture and our own small annotation layer (no dependency), sent as a chat attachment first, so it works today; the "comment on a task with a picture" step waits on the daemon (1-2 and 1-4 share one change to the task's comments).
2. **1-3 tags** and **1-4 history** are daemon units that unblock the views; the PC and phone parts follow them.
3. **1-5 time bar** reads state events; it needs the segments from the daemon (T3853).
4. **1-6 canvas** reads what exists (`blocked_by`, `state`); it can start on the fake and add `tasks.graph` later.

## 5. To agree with the founder before any child starts

1. Is "a comment can carry a picture, and a task's spec is its first comment" the right rule (§3)?
2. Capture on PC only, the phone attaches and circles (no window capture on the phone)?
3. Our own annotation layer (pen, arrow, box, text) rather than Excalidraw (MIT, bigger)?
4. React Flow (MIT) for the canvas, the phone gets a sorted list?
5. History is append-only and visible to everyone: no delete, a fix is a new row?
6. Approve is `task.accept` and Send back is `task.reject` with the circled picture, no new verbs?
7. Waiting reasons (founder, task, account, machine) are one field of the task, shown the same on the task page, the graph edge and the time bar?
