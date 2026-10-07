# Design gaps: the running web app against the Orca screenshots

The founder: "so much to improve that it's hard to believe it was built from those screenshots". The founder also said: the design system itself is poor and has to be fixed first; it should feel like a familiar IDE, be beautiful, and be understood with little or no text. The founder is colour-blind (a founder note on the rail), so no state is shown by colour alone.

Orca shots are `docs/img/design-gaps/orca/S##.png` (1600 px wide copies of the originals; numbers from `docs/orca-feature-inventory.md`). Ours are `docs/img/design-gaps/ours/`, taken from `main` at 961af799 with the fake daemon at 1280 px. Sizes: S under half a day, M a day or two, L longer. Orca measurements below are read off the screenshots by eye and given at a 1280 px window (Orca's own window is 2560 px wide, so its sizes are halved).

## 1. Design system proposal (fix this first)

Every gap in §3 is mostly one of these tokens being wrong, so a builder lands §1 before any screen work. It lives in one file, `apps/web/src/tokens.css`, which `styles.css` and every screen read; today `styles.css` is 2280 lines with 5 colours, 9 font sizes, 6 radii and raw hex in places.

### 1.1 What Orca does, and what ours does instead

| | Orca (S01, S11, S12, S36, S46) | Ours today (`styles.css` :root) |
|---|---|---|
| Surface | Dark first, neutral grey with no hue; three layers (canvas, sidebar, raised card) that differ by a few percent | Light by default and dark only by OS setting; one `--panel` over `--bg`, warm hue |
| Text | White, a dimmer grey for the second line, a third grey for hints; one family | `--ink` and `--muted` only; any third level is an opacity guess |
| Accent | None for chrome. Primary button is a pale solid pill; selection is a lighter grey, never blue | `--accent` blue on selection, links and focus |
| State | An icon per state (bell, filled dot, spinner, check ring, branch glyph), amber and green only as a second cue | A 9 px coloured dot, `.dot.running/.waiting/.blocked` (`styles.css:102`); `.gauge-fill`, `.cost-seg`, `.tchip` and `.hr-state` fill or tint by colour |
| Icons | One outline set, one size, on every row, tab, menu item and status-bar cell | Characters: `○ ● ✕ ⚠ ☰ ＋ ✎ ⚙ ▾ ▶ ▲` in 4 files; no icon in menus |
| Density | Rows 24 px single line, 44 px two line; tab bar 32; status bar 24 | Rows grow with their text; `padding: 8px 10px` and `10px 12px` on most things |
| Words | Names and numbers only. Everything else is an icon with a tooltip | Labels and sentences on cards and rows |

### 1.2 Colour tokens

Neutrals carry the whole UI. Hue is kept for four meanings and always sits beside a shape (§1.3). Dark is the default; light follows `prefers-color-scheme` and a manual switch in Settings.

| Token | Dark | Light | Used for |
|---|---|---|---|
| `--bg-0` | `#0e0e10` | `#ffffff` | Canvas behind panes |
| `--bg-1` | `#151517` | `#f7f7f8` | Sidebar, status bar, tab bar |
| `--bg-2` | `#1c1c1f` | `#efeff1` | Cards, inputs, popups |
| `--bg-3` | `#26262a` | `#e4e4e8` | Hover and selected row |
| `--line` | `#2b2b30` | `#e4e4e7` | 1 px dividers |
| `--line-strong` | `#3d3d44` | `#c9c9d0` | Input and popup borders, focus ring base |
| `--ink` | `#ececee` | `#18181b` | Names, titles, values |
| `--ink-2` | `#a3a3ab` | `#52525b` | Second line, labels |
| `--ink-3` | `#6f6f78` | `#8a8a94` | Hints, units, placeholders (never for a state; contrast 4.5:1 on `--bg-1` or it moves up a level) |
| `--primary` / `--on-primary` | `#ececee` / `#0e0e10` | `#18181b` / `#ffffff` | The one solid button per view (Orca "New Automation", "Create worktree") |
| `--focus` | `#7aa2ff` | `#2f5fd0` | Focus ring only, 2 px |

State hues are the Okabe-Ito set, chosen because they stay apart under red-green colour blindness; they are never the only signal:

| Token | Dark | Light | Meaning |
|---|---|---|---|
| `--s-working` | `#56b4e9` | `#0072b2` | Working |
| `--s-wait` | `#f0b429` | `#b87900` | Needs a person |
| `--s-bad` | `#e8743b` | `#c2410c` | Blocked, failed, over limit |
| `--s-ok` | `#3fbf9b` | `#00875f` | Done, healthy |

Replace `--run/--wait/--block/--idle` with these. Remove `--accent` as a chrome colour.

### 1.3 Never colour alone: one glyph per state

One `<State>` component draws the glyph, the tooltip and the `aria-label`; no screen draws its own dot.

| State | Glyph (16 px, 1.5 px stroke) | Motion | Extra cue |
|---|---|---|---|
| Working | Open arc, spinning | Rotates 1 turn per 1.2 s; static under `prefers-reduced-motion` with a fixed arc | Row name in `--ink`, not `--ink-2` |
| Booting / waking | Dashed ring | Pulses once per 2 s | |
| Needs a person | Bell | None | Row gets a 2 px left bar in `--s-wait` and sorts first |
| Blocked, failed | Triangle with bar | None | Left bar in `--s-bad` |
| Idle, parked | Hollow ring | None | Row name in `--ink-2` |
| Done | Check in a ring | None | |
| No terminal | Terminal glyph with a slash | None | |

Rules for builders: (1) a state is a glyph, then a tooltip, then optionally a word; (2) a series in a chart gets a shape or a pattern, not just a hue (`.cost-seg c0..c3`, `Outlook.tsx`); (3) a bar always prints its number (the gauge already does); (4) `fnote.bad`, `fnote.warn`, `field-note.bad` start with an icon.

### 1.4 Type

One sans family (`Inter`, then `system-ui`) and one mono (`ui-monospace`). Sizes are tokens; nothing else is allowed (ours has 10, 11, 12, 13, 14, 15, 18, 20 and 26 today).

| Token | Size / line | Weight | Used for |
|---|---|---|---|
| `--t-hint` | 11 / 14 | 400 | Meta line, units, table header (uppercase, +0.04 em) |
| `--t-body` | 13 / 18 | 400 | Everything that is read |
| `--t-name` | 13 / 18 | 500 | Agent, task and tab names |
| `--t-title` | 15 / 20 | 600 | Dialog and section titles |
| `--t-page` | 20 / 26 | 600 | Page title (Orca "Automations", "Agents") |
| `--t-mono` | 12 / 16 | 400 | Ids, paths, sha, terminal |

Numbers use `font-variant-numeric: tabular-nums` so gauges and counts do not jump.

### 1.5 Space, density, radius, size

- **Space** on a 4 px grid: 2, 4, 6, 8, 12, 16, 24, 32. Anything else is a bug.
- **Rows**: 28 px one line, 44 px two line (name over a hint), 52 px in a table. Row padding 0 8 px; the gap between icon and text is 8 px.
- **Chrome**: tab bar 32, toolbar 32, status bar 24, button 28 (small 24, large 32), icon button 28 square, input 32, dialog field 36.
- **Sidebar**: 240 px, resizable 200 to 360, collapses with `⌘B`. Ours is a fixed 280 px (`styles.css:906`) with 14 px of padding.
- **Content**: a settings or form page is one centred column, 640 px wide (Orca S12, S36). Lists and tables use the full width.
- **Radius**: 4 chips and inline controls, 6 buttons, inputs and rows, 8 cards and popups, 12 dialogs, 999 toggles and dots. Ours uses 3, 5, 6, 8, 10 and 50%.
- **Icons**: Lucide, one set, 16 px (14 inline in text, 20 in an empty state), `currentColor`, 1.5 px stroke, no emoji.
- **Shadow**: popups and dialogs only, `0 8px 24px #0006` dark and `0 8px 24px #0002` light, plus a 1 px `--line-strong`. Cards have no shadow. <!-- local-ref-ok -->

### 1.6 Pane chrome, tabs, toolbar

- **Tab bar** (S11, S52): 32 px on `--bg-1`; a tab is an icon, a name (max 18 characters, ellipsis), and a close `x` that shows on hover; the active tab has a 2 px `--ink` underline, not a fill. `+` opens a menu (S52): a search field, then rows with icon, name and shortcut in `--ink-3`.
- **Right of the tab bar**: one text button for the main action (Orca "Command") and one icon for the context panel.
- **Toolbar**: 32 px, filters as icon buttons and pills (S06: "Issues / PRs / Projects" pills, "Filters", refresh). A row of words becomes a row of icons with tooltips.
- **Buttons**: ghost by default; outline when it is one of a group; solid `--primary` for one per view; icon-only is a 28 px square with a tooltip.
- **Controls**: switch 28 x 16; segmented control for 2 to 3 options (S12 "On / Agent / Off", "Yolo / Manual"); select with a chevron; pills for filters.
- **Popups** (S46, S47, S52): 8 px radius, header with title and refresh, rows 28 px, a chevron when a row opens a second popup to the right.
- **Dialog** (S10): dimmed and blurred backdrop, 460 px, 12 px radius, title 15, label 12 `--ink-2` above a 36 px field, one solid button at the bottom right with its shortcut shown, `Esc` closes.
- **Status bar** (S01): 24 px; each cell is an icon plus a number, a popup on click; the words go in the tooltip.
- **Empty state** (S11, S01): a 20 px icon in a circle, one line of `--t-name`, one line of `--ink-3`; nothing else.

### 1.7 The "no text" rule

A person reading the screen should not need a sentence. Check each view with this order: icon, then count, then name, then tooltip; a sentence only for what the user wrote or for an error they must act on. Candidates for words to drop are listed per screen in §3.

## 2. Top 10 by harm to daily use

Ranked by how often the founder meets it and how much it costs. IDs point into §3.

| # | Gap | Why it hurts | Size | Group |
|---|---|---|---|---|
| 1 | **G01** State is a coloured dot on every screen | The founder is colour-blind; "blocked", "idle", "working" and "needs you" look alike in the sidebar, Team, Tasks and Accounts | M | A |
| 2 | **G02, G03, G04** No design system: default blue links in the nav, white native inputs, native selects and checkboxes, a warm brown surface | The app looks unfinished on its first screen; nothing matches Orca's neutral, even layers | L | A |
| 3 | **G20, G21** Agent page: chat and terminal always side by side at 500 px each, two tab rows, an empty black terminal under a key strip | The page the founder lives in has the least room and the most chrome (S11, S52) | M | C |
| 4 | **G12** Usage and daemon status are three lines of text at the bottom of the rail | Orca keeps them in a full-width bar with meters and counts that open popups (S01, S46) | M | B |
| 5 | **G40** Inbox cards are 130 px tall with 3 to 5 text buttons each | Three items fill the screen; triage means scrolling (S06 shows 11 rows) | M | E |
| 6 | **G30 to G34** Tasks: 8 text columns, a checkbox on every row, a toolbar of 6 native controls, and the detail pane squeezes the list into two toolbar rows | The daily list is dense with words and cramped when a task is open (S06) | M | D |
| 7 | **G23, G25** Composer is a two-line instruction and a big Send; the bridge's send-now, cancel-and-restore, message-number links, `/` and `!` are missing | The bridge is still the faster place to type to an agent (bridge tree short list 1 to 4) | L | C |
| 8 | **G11, G14** No top bar with back, forward, sidebar toggle and a real search | Orca's search is one box for tabs, history, files, URLs, agents (S01, S52) | M | B |
| 9 | **G50** Team is a boxed-button table with "role file updated" and "base file updated" columns | The place where model and account now belong (the sidebar task item 3) is a file-date table | M | F |
| 10 | **G81** Agent settings is a long native form: white inputs that read as disabled, unit words, a note under each field | Reached on every agent change; it looks broken (S12) | L | H |

## 3. Gaps per screen

Each row: ID, Orca shot (S), ours (file in `docs/img/design-gaps/ours/`), what differs, the fix in one line, size. B marks an item from the bridge feature tree (`docs/bridge-feature-tree.md` at 91e59587, `[web: part]` or `[web: no]`). Sidebar rows (name, role text, model, account, context and cache, folding, filter) are the sidebar task and are not repeated.

### 3.0 Side by side

| Screen | Orca | Ours |
|---|---|---|
| Frame, nav, status bar | ![S01](img/design-gaps/orca/S01.png) | ![home](img/design-gaps/ours/home-1280.png) |
| Agent page, chat | ![S11](img/design-gaps/orca/S11.png) | ![agent](img/design-gaps/ours/agent-architect-1280.png) |
| New-tab menu | ![S52](img/design-gaps/orca/S52.png) | not captured |
| Tasks table | ![S06](img/design-gaps/orca/S06.png) | ![tasks](img/design-gaps/ours/tasks-1280.png) |
| Task detail | ![S06](img/design-gaps/orca/S06.png) | ![task](img/design-gaps/ours/task-1688-1280.png) |
| Inbox | ![S06](img/design-gaps/orca/S06.png) | ![inbox](img/design-gaps/ours/inbox-1280.png) |
| Team | ![S45](img/design-gaps/orca/S45.png) | ![team](img/design-gaps/ours/team-1280.png) |
| Accounts and usage | ![S46](img/design-gaps/orca/S46.png) ![S47](img/design-gaps/orca/S47.png) | ![accounts](img/design-gaps/ours/accounts-1280.png) ![alpha](img/design-gaps/ours/accounts-alpha-1280.png) |
| Stats | ![S36](img/design-gaps/orca/S36.png) | ![stats](img/design-gaps/ours/stats-1280.png) |
| Settings | ![S12](img/design-gaps/orca/S12.png) | ![settings](img/design-gaps/ours/settings-1280x1400.png) |
| Agent settings | ![S12](img/design-gaps/orca/S12.png) | ![agent settings](img/design-gaps/ours/agent-term-build-settings-1280.png) |
| Create dialog | ![S10](img/design-gaps/orca/S10.png) | not captured (`NewTask.tsx`) |

### 3.1 Everywhere

| ID | S | Ours | Gap | Fix | Size |
|---|---|---|---|---|---|
| G01 | S01 S11 S44 | `home`, `team`, `tasks`, `accounts` | State is a 9 px dot in `--run/--wait/--block/--idle`; Team shows a red dot on an agent that is just "idle" and a grey one on another "idle" | One `<State>` glyph per §1.3, used by every screen | M |
| G02 | S01 | `home` | Nav is split over two rows with mixed styles: Inbox, Tasks, Team are white text and Accounts, Stats, Settings are default blue underlined links | Rail nav as icon rows with a count (Inbox) and tooltips, one style | S |
| G03 | S12 S18 | `settings`, `agent-term-build-settings`, `tasks` | Native `<select>`, checkbox, number input and white text input | `Field`, `Select`, `Switch`, `Segmented` from §1.6 | M |
| G04 | S01 S12 | all | Warm brown-black surface and a single `--panel`; Orca is neutral with three layers | `tokens.css` §1.2 | S |
| G05 | S01 S52 S46 | all | No icons: the nav, tabs, buttons, menus and status are words | Lucide set, §1.5 | M |
| G06 | S01 | all | 9 font sizes, 6 radii and arbitrary paddings; rows have uneven heights (home vs team vs tasks) | Tokens §1.4, §1.5; lint that rejects raw px in screens | M |
| G07 | S12 | `accounts`, `stats`, `settings` | Content is left-aligned in an arbitrary width (Accounts 870, Stats 870, Settings 610); each screen picks its own | One page container: 640 centred for forms, full width for lists | S |
| G08 | S52 S35 | all | No keyboard hints; bridge `?` lists the keys of each screen and exists only on Inbox and Tasks | B: `?` on every screen; shortcut shown in tooltips and menus (S52 shows them in `--ink-3`) | M |

### 3.2 Shell: rail, top bar, status bar (group B)

| ID | S | Ours | Gap | Fix | Size |
|---|---|---|---|---|---|
| G10 | S01 | `home` | Rail is 280 px, heading "Handline" with a hamburger and an orange count badge, then two rows of links, a search field, a role select and "hide parked" | 240 px, Orca order: window row (toggle, back, forward), Search, Inbox, Tasks, Team, Accounts, Stats; settings and help as two icons bottom left | M |
| G11 | S01 | `home` | No back, forward or sidebar toggle in a top bar; the hamburger is the only control | Top bar with history arrows (the hash router already has history) and `⌘B` | S |
| G12 | S01 S46 S48 S49 | `home` | Daemon, agent count, running and account percentages are three lines of text at the rail bottom ("acct alpha 41% · review 88% · gamma –") | Full-width 24 px status bar: daemon glyph, per account a mini gauge with the percent, counts with icons; each opens a popup. the sidebar task item 10 shrinks the rail's bottom; this moves it to a bar | M |
| G13 | S46 S47 | not captured | Usage popup with Detailed/Compact and a second popup per provider; `UsagePopup.tsx` exists but was not captured (needs a click) | Compare when term-build can script a click | S |
| G14 | S01 S52 | `home` | "Search agents" only filters the list; Orca's search spans tabs, history, files, URLs and agents | `⌘K` palette: agents, tasks by number or title, inbox, pages, actions | M |
| G15 | S44 | `home` | The role select ("by role") and "hide parked" are words; the sidebar task item 9 asks for icon filters | Covered by the sidebar task | n/a |

### 3.3 Agent page: chat and terminal (group C)

| ID | S | Ours | Gap | Fix | Size |
|---|---|---|---|---|---|
| G20 | S11 S52 | `agent-term-build`, `agent-architect` | Chat and terminal are always side by side at about 500 px each; the terminal is a black box with a chip "input: daemon" and, below, a key strip (Esc, ^C, ↵, ↑, ↓, Tab, ⇧Tab, 1, 2, 3) that is always visible | One pane group with tabs (Chat, Terminal, Bytes), split only on request; the key strip becomes a keyboard icon that opens a popover (it matters on touch, not on desktop) | M |
| G21 | S11 S52 | `agent-architect` | Two tab rows: panes "Chat" and "Claude Code" with `+` and a grid glyph, then "Mine / All" under Chat; only some tabs have a close `x` | One row; "Mine/All" becomes a two-icon segmented control inside the chat header; every tab has icon, name, hover `x`; the `+` menu as S52 | S |
| G22 | S11 | `agent-architect` | Every message carries a line of words ("08:32 PM · Answered", "09:17 PM · Typed Withdraw", "Not answered"); the tool line "Read docs/pc-app-gaps.md" is monospace and grey with a small glyph | Time on hover; delivery as a glyph (queued, typed, answered, withdrawn) with a tooltip; one line per tool call with its icon | M |
| G23 | S11 | `agent-term-build`, `agent-architect` | Composer placeholder is an instruction that wraps ("Message the agent (Enter sends, Shift+Enter for a new line)"); Send is a large text button | "Send a message…", a `+` attach icon, model and effort chips, a round send icon with the keys in its tooltip | M |
| G24 | S11 S01 | `agent-term-build` | Empty chat is two plain sentences ("No messages yet", "Write to … below") | Empty state per §1.6: icon, one line, one hint | S |
| G25 | | `agent-architect` | Bridge chat features missing. See the B rows below | See below | L |
| B1 | | `agent-architect` | Queued message: "Withdraw" link only. Bridge: send now (types it at once while the agent works) and cancel, which puts the text back in the box [web: no] | Two icon buttons on a queued message: send now, take back | M |
| B2 | | `agent-architect` | A message number is not a link; no "↓ N new" bar when scrolled up; older history loads from an "Older messages" button, not on scroll [web: no / part] | `#N` links that scroll to the message (the `chat.locate` method), a "N new" pill, load on scroll | M |
| B3 | | `agent-architect` | No `/` menu (wake, close, restart, park, fold, move, terminal) and no `#` menu to insert a task or request number [web: no] | Slash and hash menus in the composer, same verbs as the agent row | M |
| B4 | | `agent-architect` | No `!command` shell card, no `Ctrl+R` history menu, no Esc-twice to clear a draft; arrows recall only [web: no / part] | Add the three; keep per-agent history | M |
| B5 | | `agent-architect` | The tree says the web has no "All" tab; `ChatTab.tsx:65` has one (Mine, All), so what is missing is replying to a message between two other agents [web: part] | Check the All stream against the bridge; reply from any message | M |
| B6 | | `agent-architect` | Chat strip with agent, account, model, state, context bar and cache clock above the transcript [web: part] | A one-line strip of icons and two small gauges (context, cache), details in tooltips | M |
| B7 | | `agent-architect` | Sandbox and stall banners on the chat page [web: no] | Banner row with an icon and one action | S |
| B8 | | `agent-architect` | Inline HR cards approve or reject where the proposal was discussed [web: part] | Card with the decide buttons as icons, link to Team > HR | S |

### 3.4 Tasks (group D)

| ID | S | Ours | Gap | Fix | Size |
|---|---|---|---|---|---|
| G30 | S06 | `tasks` | Table of 8 text columns (#, P, state, title, owner, idle, timeout, asker) with a checkbox on every row; title is cut ("Terminal pane on the bytes chan…") | Orca table: ID, two-line title (title over "owner · asker"), state pill with glyph, updated; idle and timeout in a tooltip; checkboxes only in select mode; row action "Start/Open →" and `⋮` | M |
| G31 | S06 | `tasks` | Toolbar: "all agents" select, "list/tree" toggle, "sort: P" select, "states" button, "closed" checkbox, "?" | Pills for state groups (Open, Mine, Closed), a Filters popover, view toggle as two icons, sort in the column header, `?` as an icon | M |
| G32 | S06 | `tasks` | No search box; Orca has `is:issue is:open` search, `+` and refresh icons | Search over title, number, owner; `+` new task; refresh | S |
| G33 | S06 | `task-1688` | Opening a task adds a 400 px detail column: the list's toolbar wraps to two rows, the asker column drops and the detail shows owner, asker, priority, time box, "1h left", created, delivered and "under" its parent task as one run-on text block | Detail as a right drawer over the list, or a full page; fields as icon plus value in a 2-column grid | M |
| G34 | S06 | `tasks` | A permanent bottom bar says "acts on the row under the cursor" next to four disabled-looking buttons (Discard, Owner, Priority, Merge) | Row `⋮` menu and hover icons; the bar goes | S |
| G35 | S06 | `task-1688` | Task actions are text buttons with key hints ("Done x", "Assign a note a", "Edit e", "Cancel c", "Discard d"), three of them disabled | Icon row with tooltips; hide the unavailable ones | S |
| G36 | | `task-1688` | "Delivery history" and "Memos" are text lists; "memo to no one" placeholder | Timeline with icons; "Add a memo" | S |

### 3.5 Inbox (group E)

| ID | S | Ours | Gap | Fix | Size |
|---|---|---|---|---|---|
| G40 | S06 S45 | `inbox` | Each item is a 130 to 150 px card with a kind chip, agent, age, a bold title, a body and 3 to 5 text buttons (Reply…, Open task, Mark read, Dismiss, Yes, No, Answer…); 4 items fill the page | 52 px rows (glyph, title, agent, age) with hover icons; opening a row shows the body and answer controls (built inline, under the row: the list stays whole and a side panel would squeeze it) | M |
| G41 | S06 | `inbox` | Toolbar: "All kinds" and "All agents" native selects, "Mark all read", "?" | Pills and icon buttons; filters in a popover | S |
| G42 | S06 | `inbox` | The read/unread cue is a filled blue dot against a hollow ring, which is a shape difference and stays; read items also dim, and the dimmed text is hard to read | Keep the shape; dim to `--ink-2`, not lower | S |
| B9 | | `inbox` | FOR YOU shrinks as chats are read (`chat.seen`) [web: no] | Opening a chat marks its items read | S |

### 3.6 Team (group F)

| ID | S | Ours | Gap | Fix | Size |
|---|---|---|---|---|---|
| G50 | S45 | `team` | A table whose column headers (agent, role, reports to) and agent names are drawn as boxed buttons; columns "role file updated" and "base file updated" ("not set") fill the right; the table stops at 800 px of a 1000 px area | Orca board idea (S45): one card per agent with glyph, name, role, account, model, current task; group by "reports to"; file dates in the card's menu | M |
| G51 | S45 | `team-hr` | HR tab reads "No proposal is waiting for your decision." in plain text | Empty state per §1.6; proposal cards when present | S |
| G52 | S45 | `team` | Role and model/account are text; the sidebar task item 3 moves model and account here | Show account and model as icon plus short name on the card | S |

### 3.7 Accounts and usage (group G)

| ID | S | Ours | Gap | Fix | Size |
|---|---|---|---|---|---|
| G60 | S46 S47 | `accounts` | Cards with two full-width bars, "resets in 1 h 11 m" under each, "agents 1 / cap 4"; the bar tone (green, amber, red) is the only sign that 88% is serious | One line per account as S46 ("5h ▬ 41%  wk ▬ 62%"), reset time in a tooltip, a warning glyph at 80% and over | S |
| G61 | S47 | `accounts-alpha` | Page has a breadcrumb "Accounts / alpha" with the link underlined, three tabs, then lines of words ("lasts about 2 d 12 h at this pace · measured", "Month: renewing", "Usage recovery: off", "Or from the host: hl login alpha") | Icon plus value rows; the host login hint in a tooltip on the button | S |
| B10 | | `accounts` | Add, cap and remove an account from the screen [web: part: login yes; add/cap/remove not seen] | Buttons: add, cap (number), remove with a confirm | M |

### 3.8 Stats (group G)

| ID | S | Ours | Gap | Fix | Size |
|---|---|---|---|---|---|
| G70 | S36 | `stats` | Four tiles with words only ("tasks done", "moves", "handoffs", "blocks"); Orca's carry an icon | Icon tile as S36 | S |
| G71 | S36 | `stats` | "Account use" repeats the Accounts page bars | Drop it; link to Accounts | S |
| G72 | S36 | `stats` | Outlook is taller than the window (the 800 px shot ends mid-chart), its controls are two native selects and a "Table" button, and the guess is a dashed line with the word "guess →" | Fit to 280 px, segmented controls, legend with a shape per series | M |
| B11 | | `stats` | Per-agent table (tokens, cost, share, "~ week %") with the agent drawn over the graph, and a cost-per-day view stacked by team that drills into agents with 7d/14d/30d [web: part] | Table under the graph; a stacked chart with drill-in | M |

### 3.9 Settings (group H)

| ID | S | Ours | Gap | Fix | Size |
|---|---|---|---|---|---|
| G80 | S12 S18 | `settings` | One page of four headings with native inputs; each setting is a bold name followed by a sentence on the same line ("Default cap agents working at once on an account…") so the name and the text run together | Left settings nav grouped as S12; each setting a row: title, one grey line, switch or segmented on the right; page title with a one-line description | L |
| G81 | S12 | `agent-term-build-settings` | White inputs with grey placeholder text that look disabled, native selects, units as words ("k tokens", "min, 0 = never"), a grey note under most fields; the form runs past two screens | Cards per section (Model, Work, Session, Browser, Schedules) with switches and segmented controls; unit as a suffix inside the field; notes in tooltips | L |
| G82 | S12 | `settings` | "Standing answers" is a checkbox list with "Add…" and "Remove selected" | Rows with a trash icon and an "Add" row | S |

## 4. State shown by colour alone

The founder is colour-blind. Each place below is fixed by §1.3; "colour only" means the colour is the only difference between two states.

| Where | What shows the state | Colour only? | Fix |
|---|---|---|---|
| Rail agent row (`Sidebar.tsx:157`, `.dot`) | Dot colour (red blocked, grey idle, hollow parked, solid) | Red and grey and orange differ by hue; hollow vs solid differs by shape | `<State>` glyph; the sidebar task item 8 |
| Daemon dot (`Sidebar.tsx:136`) | Green, red, orange dot beside "daemon open" | The word is there; the dot adds nothing for colour-blind use | Glyph for open, closed, connecting |
| Team doing column (`TeamPage.tsx:105`) | A dot beside "idle" red for one agent and grey for another, same word | Yes | `<State>` and show why (blocked) |
| Task state pill (`taskui.tsx`) | Green "doing", amber "queued", grey "draft": dot and word | No (word), but the word goes when rows get compact | Glyph plus tooltip |
| Account state (`Accounts.tsx:19`) | "in use" green, "login needed" red outline, with words | No | Keep the word; add a glyph |
| Gauge fill (`Gauge.tsx`, `.gauge-fill`) | Green, amber, red by percent; the number is printed | The tone is colour only | A warning glyph at 80% and an over-limit glyph |
| Cost chart (`.cost-seg c0..c3`, `Outlook.tsx:62`) | Four hues for the agents in a stacked bar; legend swatches | Yes | A pattern or letter per series |
| Outlook lines (`Outlook.tsx`) | Solid for recorded, dashed for guess, colour per account | Dash is a shape; account hue is colour | Direct labels at the line end |
| Needs-you row (`.row.attn`, `.bell`) | Amber left border and a bell | Border is colour; the bell is a shape | Keep the bell; sort first |
| Unread dot (`InboxCard.tsx:86`, `.inbox-dot`) | Filled vs hollow, plus `aria-label` | No | Keep |
| HR state (`HrTab.tsx:147`, `.hr-state`) | A word with a tone | No | Add the glyph |
| Diff lines (`HrTab.tsx:175`, `.hr-diff .del/.add`) | Red or green text; the `+` or `-` mark is read (`l.mark`) but not printed (`{l.text}` only) | Yes | Print the `+` and `−` mark |
| Field notes (`.fnote.bad`, `.field-note.bad`, `.fnote.warn`) | Red or amber text | Yes unless the sentence says so | Leading icon |

## 5. Not covered here

- **Sidebar rows and filters** are the sidebar task (items 1 to 10 of the founder's sidebar notes).
- **Terminal and Bytes tab, usage popup, command palette, dialogs** were not captured (no scripted click); G13, G20 and the dialogs in §1.6 come from the source and the Orca shots only.
- **Orca screens with no counterpart here**: Automations (S01 to S03), Browser, Mobile emulator, Floating workspace, Voice, SSH hosts, Remote servers, Plugins. They are product scope, not design gaps.
- **Mobile** (375 and 768) is in `docs/design/` already; this list is 1280 only.

## 6. Groups for parallel builders

Builders in different groups do not edit the same files. Group A lands first (a day); B to H then run in parallel and only read A.

| Group | Owns (files) | Gaps | Size |
|---|---|---|---|
| A. Tokens and primitives | `apps/web/src/tokens.css` (new), `ui/*` (State, Icon, Button, Field, Select, Switch, Segmented, Popup, Dialog, Empty), `styles.css` (split by screen) | G01 G03 G04 G05 G06 G07 | L |
| B. Shell | `App.tsx`, `Sidebar.tsx` (nav and status only), `Workspace.tsx`, `route.ts`, `shell.ts`, `UsagePopup.tsx`, command palette (new) | G02 G10 G11 G12 G13 G14 G08 | L |
| C. Agent page | `ChatTab.tsx`, `ChatMessages.tsx`, `ChatCompose.tsx`, `Pane.tsx`, `TermBar.tsx`, `TerminalTab.tsx`, `BytesTab.tsx`, `AgentVerbs.tsx`, `PromptCard.tsx` | G20 to G25, B1 to B8 | L |
| D. Tasks | `Tasks.tsx`, `TasksHead.tsx`, `TaskRows.tsx`, `TaskDetail.tsx`, `TaskSheets.tsx`, `NewTask.tsx`, `taskui.tsx`, `taskActions.ts` | G30 to G36 | M |
| E. Inbox | `Inbox.tsx`, `InboxCard.tsx`, `Answers.tsx` | G40 to G42, B9 | M |
| F. Team and HR | `TeamPage.tsx`, `HrTab.tsx` | G50 to G52 | M |
| G. Accounts and Stats | `Accounts.tsx`, `AccountSettings.tsx`, `Gauge.tsx`, `Stats.tsx`, `Outlook.tsx` | G60 G61 G70 to G72, B10 B11 | M |
| H. Settings | `Settings.tsx`, `SettingsView.tsx` | G80 to G82 | L |

Shared files with a rule: `styles.css` loses a block per group when that group moves its rules into `<Screen>.css`; only the group that owns the screen edits its block. `Sidebar.tsx` is split: B takes nav and status bar, the sidebar task takes the agent rows, so the two merge on separate functions.
