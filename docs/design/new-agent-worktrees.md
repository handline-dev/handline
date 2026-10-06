# Screen designs: New agent and Worktrees

Layout, states and keys for the two screens `docs/web-ui-design.md` §3.7 (Worktrees) and §3.8 (New agent / new worktree) leave as design work after U16 in `docs/pc-app-gaps.md` §6. The bridge has neither screen (`hl spawn` is a command), so there is no bridge code to copy; what the screens do comes from `handline/team/spawn.py` (what a spawn checks and refuses), and what they may call from `contract/protocol.json`. Same form and shared rules as `docs/design/inbox-tasks-prompts.md` §1: tokens from `apps/web/src/styles.css` (no new colour), skeleton with `docs/turborepo-layout.md` §8.6 timing, the four list states, the key sheet on `?`, phone under 640 px. Write kinds are those of `turborepo-layout.md` §8.5: **authored** (the value changes at once, hollow dot `○`, reverts on refusal) and **decided** (no cache change, a pending mark on the clicked control, the reason on error). Docs only: nothing here is built.

**Pending:** the numbered daemon requests N8 to N14 of §3 continue the list of `pc-app-gaps.md` §5. Each screen is drawn so that it works with today's contract and gains one control when a request is listed; a control for an unlisted method is hidden, not greyed.

**Role gates (contract).** `agent.create` is **founder-only**; `repos.list`, `worktrees.list`, `worktree.diff` and `worktree.set_status` need the member role. So New agent is founder's ("New agents are for the founder." and no button for anyone else), and Worktrees is open to a member, who may read and change a status.

## 1. New agent

### 1.1 Where it lives

A **+ New agent** button at the foot of the sidebar's agent list and a route `/new`, both founder-only. On desktop it opens as a sheet over the page at 560 px; the route is the same screen full-width (phone, or a link). While a creation runs the route is `/new/:operation`, so a reload or a shared link returns to its progress.

### 1.2 The form

```
┌ New agent ───────────────────────────────────────────────────────── [×] ┐
│ name      [ reviewer           ]   role     [ reviewer              ]    │
│ repo      [ handline        ▾ ]   base     [ main                  ]    │
│ model     [ default         ▾ ]   effort   [ default            ▾ ]      │
│ account   [ auto            ▾ ]   parent   [ none               ▾ ]      │
│ first task (optional)                                                    │
│ [ text …                                                              ]  │
│                                              [ Cancel ]   [ Create ]     │
└──────────────────────────────────────────────────────────────────────────┘
```

| Field | Control | Param of `agent.create` | Source and rule |
|---|---|---|---|
| name | text, required | `name` | The agent's name is the worktree name, the task label and the folder (spawn.py). A name already in use or not allowed is refused by the daemon (`refused` or `bad_request`) and its reason shows under the field. |
| role | text, required | `role` | A free string today (N10 asks for the allowed list); the field offers the roles of agents already shown in the sidebar as suggestions. |
| repo | select, required | `repo` | `repos.list` (`name`, `path`, `default_branch`); the select shows the name and the path in `--muted`. Choosing a repo fills **base**. A repo that is not listed cannot be added from the web until N8. |
| base | text | `base` (omitted when equal to the default branch) | Defaults to the repo's `default_branch`. spawn.py takes the side (local or `origin/`) that leads and refuses when they have diverged; that refusal arrives as the operation's failure and is shown as it is. |
| model, effort | select with "default" first | `model`, `effort` (omitted on default) | Free strings in the contract; the choices come from N10, until then a text field with the default empty. |
| account | select, "auto" first | `account` (omitted on auto) | `accounts.list`; an account that is out or needs login is shown with its state word and is still choosable. |
| parent | select, "none" first | `parent` | The agents shown in the sidebar. |
| first task | text area, optional | none (N9) | Until N9 it is sent as a second call, see §1.3. |

**Create** is enabled when name, role and repo are filled; Enter in a single-line field moves on, **Ctrl/Cmd+Enter** creates, Escape closes (with a confirm only when a field was edited: "Discard this agent?"). One request per click; the button is disabled while the call is in flight.

### 1.3 What Create does

1. `agent.create { name, role, repo, base?, model?, effort?, account?, parent? }`, **decided**. The result carries an `operation` id (a long-running operation: register, make the worktree, start).
2. The form is replaced by a **progress list** fed by the `operations` topic (`operation.progress`, snapshot `operation.get`): the operation's `step` as a line, "step 2 of 4" from `steps_done` and `steps_total` when the total is known, `message` under it. No spinner: a bar of the step count when known, otherwise the step text alone, and a quiet marker. The list never invents step names the daemon did not send.
3. **done**: the agent's row appears in the sidebar through the `agents` topic; the sheet shows "reviewer is running" with **Open agent** (primary) and **Create another**. `result` is shown as the daemon sends it (N11 asks that it carry the agent, worktree path and terminal).
4. **failed**: the form returns with every field kept and the operation's `message` in `--block` at the top, **Edit and try again** as the only primary. spawn.py can fail after the worktree exists ("worktree is ready, but starting the agent failed"); the message says what the daemon says, and N11 asks it to say what exists. Nothing is cleaned up by the web.
5. **First task**, until N9: after `done`, `task.new { owner: <name>, title, body }`, **authored**. If it is refused the agent stays created, the sheet says "reviewer is running, but the first task was not sent" with **Retry** and the text kept. The title is the first line of the text (at most 80 characters), the body the rest.

Leaving the sheet while it runs does not stop the operation: the sidebar row shows the agent as it appears, and a toast says done or failed ("reviewer is running", "Creating reviewer failed: <message>", linking back to `/new/:operation`). The web does not offer to cancel an operation; the contract has no such method.

### 1.4 States

- **Loading** (`repos.list`, `accounts.list`): the form's own skeleton, a label bar and a control bar per field at the real heights; **Create** is disabled until repos arrive.
- **No repos**: "The daemon knows no repo." under the repo field; **Create** stays off. N8 adds "Add a repo".
- **Error / not served**: the shared strip; the fields that need a missing list read as text boxes; `agent.create` missing from `hello.methods` shows "This daemon cannot create agents." and no form.
- **Disconnected**: Create disabled with "daemon not connected"; the entered text is kept.
- **Not founder**: no button; the route says "New agents are for the founder."

### 1.5 Keys and phone

`?` lists keys; there are no letter keys (the form takes text). Tab and Shift-Tab move between fields. On the phone the sheet is a full page, one column, the buttons in a bottom bar 48 px high; the progress list is the same page.

## 2. Worktrees

### 2.1 Where it lives

Route `/worktrees` (the top-bar destination `H` of `web-ui-design.md` §3.7), with `/worktrees?repo=…&owner=…` for filters and `/worktrees/:path` for one worktree (the path url-encoded). A member or the founder.

### 2.2 The list and the board

A toggle **List | Board** at the top (kept per viewer); filters **repo** and **owner** (the two params of `worktrees.list`, so a filter is a different query key) and a text search over branch and path.

```
┌ Worktrees   [ repo ▾ ] [ owner ▾ ] [ search ]            [ List | Board ] ┐
│ repo      branch              owner      parent→children   status   files │
│ handline  agent/reviewer      reviewer   —                 in review  4    │
│ handline  agent/builder       builder    reviewer → 1      in progress 12  │
```

**List** columns: repo, branch, owner (a link to the agent's workspace; "no owner" in `--muted` when `owner` is null), parent and children as links to those rows, status chip, changed files as a count with `+added −deleted` summed from `changed`. Sorted by repo then branch; the header cells sort.

**Board**: one column per status, in the order **todo, in progress, in review, done** of `web-ui-design.md` §3.7, then an **other** column for any status string outside those four (`Worktree.status` is a free string; N12 asks for the list). A card shows the branch, the owner, the repo and the changed count.

**Status** is a chip with its word (colour is never alone): todo `--muted`, in progress `--run`, in review `--wait`, done `--muted` with a check mark.

### 2.3 One worktree

A side panel on desktop (`/worktrees/:path`), a page on the phone: repo, branch, path (copy button), owner with **Open agent**, parent and children, a **Status** select, and the **changed files** table (status letter `M`, `A`, `D`, `R` or `?`, path, `+added`, `−deleted`; the letter is text, colour comes second).

Two tabs: **Files** (the table above) and **Changes**, which is **reserved and not built**: it shows "Diff review comes later." and no control. `worktree.diff` (member, read-only, `{path, file?}`) is already in the contract, so the later unit needs no daemon request.

### 2.4 Verbs

| Verb | Where | Call | Kind | Confirm |
|---|---|---|---|---|
| Change status | the select in the panel; **Move to…** menu on a board card; dragging a board card | `worktree.set_status { path, status }` | decided | none (it is a label and can be set back) |
| Open agent | panel, row, card | none (opens the owner's workspace) | none | none |

**Decided** means the card does not move when dropped: it stays where it was with the pending mark ("moving…"), and moves when the refetch of `worktrees.list` after the call shows the new status. On refusal it stays and the reason shows on the card. Dragging is never the only way: **Move to…** and the select do the same, and a keyboard user focuses a card and presses `m`.

There is **no create, remove or delete** here: a worktree is made by New agent, and the contract has no remove. Nothing here edits files.

### 2.5 States

- **Loading**: a table skeleton of eight rows (or three board columns of three cards), as the real heights.
- **Empty**: "No worktrees yet." with **New agent** (founder) beside it; with a filter on: "No worktree matches" and **Clear filters**.
- **Error / not served**: the shared strip; cached rows stay; `worktrees.list` missing hides the item.
- **Refresh**: there is no `worktrees` topic, so the list is a snapshot, refetched on mount, on reconnect, and when the person's own status write settles; a change made by an agent shows at the next of these (N13 asks for a topic). A "refreshed 40 s ago" note shows beside the toggle when the data is more than 30 s old, with a **Refresh** button.
- **Disconnected**: status controls disabled with "daemon not connected".

### 2.6 Keys and phone

List: `j` and `k` move, Enter opens, `o` opens the owner, `m` moves status, `/` focuses search, `b` flips List and Board, `?` lists them. Phone: the list becomes cards (branch, owner, status, count); the board is a horizontal row of columns that snaps one column at a time; no dragging, **Move to…** only; the panel is a full page.

## 3. Daemon requests

| N | Request | Why |
|---|---|---|
| N8 | `repo.add { path }` (founder), authored on `repos.list` | the dialog's "repo (pick or add)": today it can only pick |
| N9 | `agent.create` takes `prompt` (the first task) and sends it as a task for the new agent | one call, no half-created state; today the web sends `task.new` after `done` |
| N10 | The lists of allowed roles, models and efforts (per vendor) the daemon will accept | `role`, `model` and `effort` are free strings in `agent.create` |
| N11 | `Operation.result` of `agent.create` carries `{agent, worktree, terminal}`, and a failed one says what exists (worktree made, session not) | the done and failed views of §1.3 |
| N12 | `Worktree.status` becomes an enum, or a call lists the valid statuses | the board's columns; today any string is accepted |
| N13 | A `worktrees` topic (`worktree.changed`, `worktree.removed`) | a live board; today a snapshot |
| N14 | `agent.create { dry_run: true }` returning the planned worktree path, the chosen base and why (the `--dry-run` of `hl spawn`) | a **Preview** button before Create; hidden until listed |

## 4. What the tests check

- New agent: Create is off with a required field empty and sends one call; optional params are omitted at their defaults; `base` follows the repo's default branch; the progress list shows each `operation.progress` event and no invented step; `failed` returns the form with the fields kept; `done` shows Open agent; without N9 the first task goes as one `task.new` after `done` and a refusal keeps the text; the whole screen is absent for a member.
- Worktrees: columns from fixtures for each of the four statuses plus an unknown one in **other**; a null owner reads "no owner"; the status call is sent once per drop and the card moves only after the refetch; a refusal leaves it in place with the reason; filters become the `repo` and `owner` params; Changes shows no control; a member can change a status.
- All: skeleton timing and heights of §8.6, no spinner in the DOM, light and dark, reduced motion, screenshots at 375 and 1280.

## 5. Assumptions to confirm

1. New agent is a sheet plus a route `/new`, not a top-bar item; Worktrees is a top-bar destination as `web-ui-design.md` §3.7 lists.
2. The first task is sent as `task.new` after the agent is running until N9. The bridge's `--prompt` types text into the new agent; the contract has no such parameter, and a task is what the rest of the web uses.
3. Only the founder creates agents. `protocol.md` still asks whether a manager may spawn a report; if the answer is yes the screen follows the gate with no change.
4. A board drop is a decided write (the card waits for the refetch), not an optimistic move, because only the daemon decides a status.
5. The four statuses of the design are the board's columns, and any other string goes to **other**.
6. The Changes tab is not built, though `worktree.diff` exists, because `web-ui-design.md` §3.7 puts diff review later.
7. There is no remove or cancel on either screen: the contract has no method to remove a worktree or cancel an operation.
