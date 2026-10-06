# Screen designs: Inbox, Tasks and prompt cards

Layout, states and keys for units U9 (prompt cards), U10 (Inbox) and U11 (Tasks) of `docs/pc-app-gaps.md`, inside the frame of `docs/web-main-view.md` (Inbox and Tasks are routes that replace the main view, not tabs) and `docs/web-ui-design.md` §3.3-3.4. Tokens are those of `apps/web/src/styles.css`; no new colour is added. Keys are those of the bridge (`tasks_view.py`, `chat_page.py` task detail, `tui.py` `MESSAGE_KEYS`). Docs only: nothing here is built.

## 1. Rules common to all three

**Tokens.** Page `--bg`, cards and table `--panel`, rules `--line`, text `--ink`, secondary `--muted`, focus ring and primary button `--accent`. State colour reuses the dot palette: running `--run`, waiting `--wait`, blocked or failed `--block`, idle or done `--idle`. Ids, times and commands in `--mono`. A state is never colour alone: each carries its word (see §3.3).

**Skeleton, never a spinner.** First load of a list or a detail draws grey bars (`--line` at 60% over `--panel`) in the exact row height and column widths of the loaded view, so nothing jumps when data lands. Row count: as many as fit, capped at 8. A slow pulse (opacity .6 to 1, 1.6 s) runs only under `prefers-reduced-motion: no-preference`; otherwise the bars are still. A skeleton shows only while there is no data at all; a refetch with data on screen keeps the data (no skeleton, no dimming). Buttons never show a spinner either: a write shows its pending mark (§2).

**The four states of every list**, in one block with the page title kept:

| State | Shows | Action |
|---|---|---|
| Loading | skeleton rows | none |
| Empty | one line of what this is and why it is empty ("Nothing waits on you."); no illustration | a link to where things come from (Tasks: New task) |
| Error | a strip under the title in `--block`: what failed in words from the daemon's error, "Retry" button; the last good rows stay visible and dimmed to 70% if there are any | Retry, and automatic retry on reconnect |
| Not served | "This daemon does not serve <thing>." in `--muted`, no controls (as `web-main-view.md` §3 does for tabs) | none; decided by `hello.methods` |

Disconnected is the app's, not the page's: the status bar says it, the page keeps its rows, and writes are disabled with the reason as the tooltip, "daemon not connected".

**Pending marks for writes.** The app writes optimistically only where the outcome is decided by the call alone (mark read, withdraw, queue and draft, owner and priority change, discard); anything the daemon may refuse for a state we cannot see (answering a prompt, done, reopen, merge) waits for the reply. Looks:

- *Optimistic, in flight*: the row shows the new value in `--muted` italics with a small hollow dot `○` in front of the changed cell; the row's controls stay usable except the one in flight.
- *Settled*: the dot goes and the value turns `--ink`, without animation.
- *Refused or failed*: the value reverts, the cell shows `--block` text "not changed" for 6 s and the reason from the daemon sits in a toast with the call's name ("owner: <reason>"). Never silent.
- *Waits for reply*: the clicked button reads "…" in place of its label and is disabled; its row is not dimmed. A reply slower than 10 s adds "still waiting" in `--muted` beside it; nothing is abandoned automatically, because a write may have landed.
- A change made from another tab or device arrives through the topic and replaces the row without a mark.

**Keys.** Every key works only when no text field has focus. `?` opens the key sheet (the bridge's `question_mark`), listing exactly this page's keys. Arrow keys and `j` `k` move the row cursor; Escape leaves a detail, closes a sheet, then clears marks. Every key is also a visible button (the bridge's action bar), because phones have none. A confirmation is a small inline sheet under the row, not a modal: it names the thing, the verb, and takes Enter or Escape.

**Phone width (under 640 px).** One column, bottom tab bar as in `web-ui-design.md` §7. Tables become cards (§3.4); the action bar becomes a sticky bottom bar above the tab bar with the verbs that fit and `⋯` for the rest; sheets slide up from the bottom; hit areas are at least 44 px; no hover-only control; keys are not shown, since there is no keyboard (the sheet text omits them).

## 2. U9 Prompt cards

One component, in the Chat tab and in Inbox, for the daemon's `Prompt` (`prompt.list`, answered with `prompt.answer`). It extends today's `PromptCard` (`ChatTab.tsx`).

**Anatomy**, top to bottom: title row (`⚠` and the kind: "Permission: <tool>", "Question", "Dialog"; at the right, the agent name in Inbox and "waiting 3 m" from `since`); the body; the options; a foot line in `--muted`.

- **Permission**: the command in `--mono` on `--bg` with `--line` border, wrapped, never cut, scrolls past 12 lines; the reason in `--muted`. Options **Yes** (primary) and **No** (plain). There is no "always" here.
- **Dialog**: the screen lines as `<pre>` (same limits). Options are the daemon's, in its order, the highlighted one primary. "Always" options, which save a standing rule, are marked with `↻` and a one-line note under the row, "saves a rule for next time", and are offered only for kind dialog and only when the daemon serves `answers.*` (D6). Otherwise the option is omitted, not greyed.
- **Question** (AskUserQuestion): the question text; options as a numbered list `1 2 3` (the number is the key); a last row "Type something" opens a one-line field. Multi-select (needs N6): checkboxes and a "Submit" button; without N6 the card shows the options as single choice and does not offer submit.
- **Unknown kind**: the lines as `<pre>` plus the options the daemon sends; no inference.

**Keys on a focused card**: the option's own key (`y` `n`, `1`..`9`); Enter answers the highlighted one; Escape returns focus to the page. Each option shows its key as `<kbd>`. In Chat the card sits above the message box, in Inbox it is the item's body.

**States.**
- *Loading*: a card-shaped skeleton: title bar, three body bars, two button bars. Only for an item opened from a link before its data is here.
- *Sending*: waits for the reply (§1); the other options disable.
- *Stale* (the daemon says "the dialog is gone or changed"): nothing was sent; the card shows "This prompt changed" in `--wait`, refreshes to the new prompt in place, and keeps focus. If there is none, the card collapses to "Answered elsewhere" in `--muted` for 4 s, then goes.
- *Error* (network): the options come back with "Not sent. Retry" under them; the same key resends once the connection is back, never automatically, because an old answer to a new prompt is the harm.
- *Answered*: the card folds to one line "Yes · <tool> · by <who>" in `--muted`; in Chat it stays as a transcript line.

Phone: full-width card; options stack as full-width 48 px buttons, the primary last (nearest the thumb); the command block scrolls sideways inside its own box, the page does not.

## 3. U10 Inbox

Route `/inbox`, `/inbox/:item` for a deep link (a notification opens the card). Data: `inbox.list` and topic `inbox`; the bell count in the top bar and the browser tab title comes from the unread count.

### 3.1 Layout (desktop)

```
┌ Inbox · 3 unread ───────────────────── [kind ▾] [agent ▾] [Mark all read M] ┐
│ ● Decision   an agent           asked 12 m        ← unread dot, kind chip  │
│   Should the daemon relay...                                                  │
│   ( Yes )  ( No )  [ Answer… ]                      [ Mark read m ] [ ⋯ ]    │
│ ─────────────────────────────────────────────────────────────────────────── │
│ ○ Result     an agent           done 1 h                                  │
│   Audit of the lint doc: PASS                                          [ Read ]   │
```

One column of cards, newest first, 760 px wide at most and centred; the page has no side panel. The header holds the title with the unread count, the two filters (kind, agent) as selects and "Mark all read". Unread items carry a `●` in `--accent` and a heavier title; read ones `○` and `--muted` text.

Per kind (from `web-ui-design.md` §3.3):
- **Result**: title, evidence (first 3 lines, "Read whole" opens it in place), "Reply / New task".
- **Decision**: the question and its options as buttons; "Answer…" opens a text field in the card (E3 later adds "in editor"); answering marks it read.
- **Prompt (permission or dialog)**: the U9 card as the item body.
- **Block**: agent, what is in the way, "since 2 h" in `--wait`; the verbs are the agent's (open its chat).
- **HR proposal**: the diff (collapsed to a count of changed lines, "Show diff") and Approve / Reject; arrives with U15, until then the item shows its title and "Open in the bridge".

A card's "open" jumps to the task or chat it came from (`task` or `agent` on the item). Selecting a row sets the cursor only; Enter opens.

### 3.2 Keys (the bridge's `MESSAGE_KEYS`)

| Key | Verb | Notes |
|---|---|---|
| Enter | open | the task or chat the item came from |
| `a` | answer | focuses the answer field or the first option |
| `e` | answer in editor | desktop wrapper only (E3); hidden in the browser |
| `x` | dismiss | optimistic, with a pending mark; the card folds with "Undo" for 6 s |
| `m` | mark read | optimistic |
| `M` | mark all read | confirm inline when more than 10 are unread |
| `?` | key sheet | |

### 3.3 States

- **Loading**: header bars and 5 card skeletons (title bar, two body bars, a button bar).
- **Empty**: "Nothing waits on you." and under it, in `--muted`, "Results, questions and blocks from your agents land here." With a filter on and no match: "No items match" with a "Clear filters" button.
- **Error**: the strip of §1; cached cards stay.
- **Not served**: `inbox.list` missing, §1.
- **Pending marks**: mark read flips the dot at once (hollow `○`, §1); dismiss folds with Undo; answer shows "Sending…" on the answered option only (it waits for the reply, since the item may have been answered by someone else) and a refusal shows "Already answered by <who>" and folds.
- **A new item** arrives at the top with a 2 s `--accent` left rule that fades (none under reduced motion), without moving the card under the pointer: if the list is scrolled, new items collect behind a "2 new" pill at the top instead of pushing.

### 3.4 Phone

Cards go full width with 16 px side gutters. Filters collapse into a "Filter" sheet; "Mark all read" moves into `⋯` of the header. Decision and prompt options become full-width buttons (U9). Swipe is not used (no hidden gesture): mark read and dismiss are buttons under the card, visible. Inbox is the first bottom tab and carries the unread count.

## 4. U11 Tasks

Route `/tasks`, `/tasks/:id` for the detail. Data: `tasks.list`, `task.get` and the actions of `pc-app-gaps.md` G4.

### 4.1 List (desktop)

```
┌ Tasks ─ [agent ▾] [view: list|tree] [sort: priority ▾] [states ▾] [+ New task] ┐
│ [ ] │ #   │ P │ state   │ title                │ owner   │ idle │ timeout │... │
│ [ ] │  14│ 2 │ ● doing │ Fix the login.. │ agent-a │ 4 m  │ 1h58m   │    │
├ Discard  Owner  Priority  Merge ─ 2 marked ────────────────────────────────────┤
```

- The table's columns follow the bridge's `HEADS`: mark, number, priority, state, title, owner, idle, timeout, asker, blocked by. Mono for number and times. Sort arrow on the sorted head; a head click sorts by it.
- Priority shows only where the daemon sends it (needs N2): until then the P column and the `p` key are absent, not disabled.
- **Tree view** (`TREE_HEADS`: mark, task, owner, state, waits on): the chain of what waits on what, indented, with a dimmed row where a task outside the filter holds a chain together.
- Filters: states (checkbox list; default is the open states without done, as `LISTED`), "closed" toggle for done and discarded, agent (limits to the tasks that agent owns or opened; absent on an agent's own page).
- Selecting a row on desktop opens the detail pane beside the table when the width is 1100 or more; below that Enter opens the full detail route (the bridge's `TaskPane.fit`).
- The action bar sits under the table and shows the marked verbs: Discard, Owner, Priority, Merge, enabled when rows are marked; with none marked they act on the cursor row.

### 4.2 List keys (the bridge's `KEYS`, twelve)

| Key | Verb | Confirmation |
|---|---|---|
| Space | mark or unmark the row | |
| `A` | mark all shown, or unmark | the head's `[ ]` does the same |
| Enter | open the detail | |
| `d` | discard, with a reason | required reason; "stop it and discard" wording when the task is running |
| `o` | change owner | pick an agent |
| `p` | priority 1 high to 3 low | needs N2 |
| `m` | fold the marked tasks into the row under the cursor | names both ends: "fold 3 into #14?" |
| `s`, `S` | sort by next column, reverse | |
| `a` | show or hide done and discarded | |
| `v` | states checkboxes | |
| `t` | list or tree | remembered per browser |
| `f` | next agent's tasks | |
| `?` | key sheet | |

### 4.3 Detail

Route `/tasks/:id`, or the pane beside the list. Under a Back link: title, state chip, owner and asker, priority, timing; then the body; the delivery history as a timeline; evidence; progress and memos (memo box at the end, "memo to no one" or to an owner as in the bridge); sub-tasks and what it waits on as links. Keys (the bridge's task detail):

| Key | Verb | Shown when | Prompt |
|---|---|---|---|
| `x` | done | task not done | "done: what came of it?" required |
| `u` | queue, or back to draft | draft or queued | none |
| `r` | reopen | done and asked by you | "reopen: why?" required |
| `a` | assign a note | always | the memo box |
| `d` | discard | not done | "discard: why?" ("stop it and discard" if running) |
| Escape | back | | |

A verb that does not apply is hidden, not greyed, and its key says the bridge's sentence in a toast ("only a done task that you asked for can be reopened"). Reasons are required: an empty reason keeps the sheet open.

### 4.4 New task

"New task" button and `n` (new in the web; the bridge makes tasks from chat): a sheet with owner, title, body, time box and, with N2, priority. Created as a draft or queued, the choice shown as two buttons; the row appears optimistically with the pending mark until the daemon returns its number.

### 4.5 States

- **Loading**: 8 row skeletons with the real column widths; the filter bar is shown at once (its controls work on arrival).
- **Empty**: "No tasks in these states." with the states chip and "Show closed" if closed ones exist; with no task at all, "Nothing has been asked yet." and New task.
- **Error / not served**: §1. A detail whose task is gone: "This task no longer exists" and a Back link.
- **Pending marks** (§1): owner, priority, queue/draft and discard are optimistic (the cell changes at once, hollow dot); done, reopen and merge wait for the reply because the daemon may refuse for a state the page cannot see; withdraw and cancel from `task.withdraw` and `task.cancel` are optimistic.
- A row under a bulk write shows its pending mark; a partial failure leaves the refused rows marked, "2 of 3 changed", with the reasons in the toast.
- Marks survive a refetch; a task that changes under the pointer keeps its row position until the pointer leaves.

### 4.6 Phone

The table becomes cards in the list's sort order: line one `#number`, state chip and priority; line two the title (two lines, then cut with …); line three owner, idle time and "waits on #n" in `--muted`. The tree view is an indented list with the same cards. Filters sit in one "Filter" sheet; sort is a select in it. Marking has no long-press: a "Select" button in the header shows checkboxes, and the action bar appears at the bottom. The detail is its own route; the bottom bar holds its verbs (done, queue, reopen, discard as applicable) with `⋯` for the rest. New task is a full-screen sheet.

## 5. What each unit's tests check (screenshots and DOM)

- U9: every kind renders from its fixture; a stale answer sends nothing and refreshes; "always" appears only for dialogs; screenshots at 375 and 1280.
- U10: every kind of item from fixtures, in loading, empty, error and not-served states; each key; a new item arriving while scrolled does not move the list.
- U11: each key of the two tables has a control and a test; the confirmation text equals the bridge's; skeleton row widths equal loaded widths; pending marks in the three outcomes of §1.
- All: no spinner in the DOM (a test greps for `role="progressbar"` and animated rotation), focus ring visible, light and dark, `prefers-reduced-motion` stills the pulse.

## 6. Assumptions to confirm

1. The founder's "skeleton loading, no spinners" also covers buttons: a pending write shows "…" in place of the label.
2. Decisions with N6 absent: multi-select shows as single choice. Alternative: hide the question's card and say "answer in the terminal".
3. Dismiss in Inbox: the bridge's `x` is final; here it folds with a 6 s Undo that only delays the write. Decide whether to delay it.
4. `n` for New task is new; the bridge has no key for it.
5. The detail pane beside the list at 1100 px follows the bridge's `TaskPane.fit`; the exact breakpoint is a guess.
6. The pending-mark glyph `○` is a proposal; any marker that is not colour alone will do.
