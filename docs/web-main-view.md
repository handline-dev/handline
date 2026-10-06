# Web main view: sidebar-only agent controls, tabs and split panes

Founder's rule: agent controls live **only in the left sidebar**, none at the top. The main view's top is a tab strip: **Chat** (cannot be closed),
the agent's **Claude Code / Codex terminal**, **simulator / emulator**, **web**, **markdown file** and so on. Any tab can be dragged to the **right or below**
to split the view, up to **four** panes; every tab but Chat can be closed.

This replaces the agent page of `docs/web-ui-design.md` §3.2 (header controls, one pane area beside the chat) and feeds units 3 and 4 of
`docs/turborepo-layout.md` (§7 below). Written against main @2f7de798 and the API spec in `contract/`.

## 1. What changes from the approved design

| Design today | Now |
|---|---|
| Agent page header with Start / Stop / Resume, model, account, task, worktree, input holder (§3.2) | **No header.** Controls move to the sidebar row (§2). The main view starts with the tab strip. |
| Chat on the left, one pane area on the right holding terminals (§3.2) | Chat is **a tab like the others**, pinned first and unclosable; it can sit in any pane. |
| Pane kinds: Claude Code, Shell, reserved Review | Tab kinds: Chat, Terminal (Claude Code, Codex, shell), Emulator, Web, Markdown, reserved Review (§3). |
| §6: F16 "no browser/emulator tabs", F26 browser dropped, F28 emulator dropped ("out of the supervising job") | **Reversed by this brief** for the tab kinds in §3. Confirm: they were dropped on purpose. |
| Dock to right, left, bottom, fill; merge into tab groups | Same drags, capped at **4 panes** (§4). |

## 2. Sidebar: everything per agent

A row is the agent's only control surface. Row text stays as in design §3.1 (state dot, state, task, account use).

- **Primary button** on the selected or hovered row: **Start** (no session), **Resume** (closed session), otherwise none.
- **`⋯` menu**: Interrupt, Exit, Close, Kill (confirm), Close terminal, Move account, Rename. Same words as design §3.2; the confirmations of §4.4 stay.
- **Selected row opens a detail block** under it: model · effort, account and the account `account_pick` would choose before a start, current task, worktree branch,
  prompt-cache deadline. This is what the old header showed.
- Selecting a row opens that agent's workspace (§4) in the main view; the URL is `/agents/:name` as in design §5.
- On narrow widths the sidebar is a drawer and holds the same controls.

What stays out of the sidebar: controls that act on a tab's content. A terminal tab's own strip carries **Take control / Release**, who holds the input and the key chips;
a markdown tab carries Reload. **No tab carries Start, Stop or Resume**, and the top of the main view carries nothing but tabs. The permission card remains a card in the Chat tab and in Inbox.

## 3. Tab kinds

| Kind | Closable | Shows | Daemon today | Daemon must add (request list, §6) |
|---|---|---|---|---|
| **Chat** | no | the agent's conversation: messages, transcript, cards, message box (design §3.2 and §8 chat data) | `chat.*`, topic `chat` | nothing |
| **Terminal: agent session** | yes (detaches; the agent keeps running) | the live text grid of the agent's session; label says Claude Code or Codex | `terminal.attach` / `history` / `resize` / `detach`, `session.*`, `input.take` / `release`, `AgentDetail.terminal` | **R1** which vendor the session is |
| **Terminal: shell** | yes (detaches; the terminal stays until Close terminal) | a plain terminal in the agent's worktree | the host can hold terminals, but no method opens or lists one | **R2** open and list terminals |
| **Emulator** (iOS simulator, Android emulator) | yes (detaches; stopping the device is a button in the tab) | the device screen, tap, swipe, type, hardware keys | nothing | **R3** device list, start/stop, screen stream, input |
| **Web** | yes | a page in an iframe: usually the agent's dev server, optionally any URL | nothing | **R4** a daemon proxy for a port in the agent's worktree |
| **Markdown** | yes | a file of the agent's worktree, rendered, read-only | nothing (`worktree.diff` reads a diff, not files) | **R5** read a file, list files, tell when it changed |
| **Review** (reserved) | yes | the worktree diff | `worktree.diff`; capability `review` is false | not now; hidden unless the capability is true |

A tab whose kind the daemon does not serve is not offered in `＋`; one restored from a saved layout shows "this daemon does not serve <kind>" and can be closed.
`hello.methods` and `capabilities` decide, as design §8 already says for Review. v1 builds Chat and Terminal on what exists now; the others arrive as R1..R5 land.

Notes per kind:
- **Terminal**: attached only while rendered. A hidden tab, or a tab of an agent that is not selected, detaches and reattaches with a fresh snapshot (the daemon polls once per terminal and stops when the last viewer leaves). The text-grid limit and the input-holder rule (design §4.3) are unchanged.
- **Web**: an iframe of a page that refuses framing (`X-Frame-Options`) shows "open in a new tab" instead; there is no scraping or rewriting. This tab is for the person; agents driving a browser stays out of scope.
- **Markdown**: sanitized rendering (no raw HTML, no scripts); relative images and links resolve through the same file-read call, confined to the worktree. Files open from `＋ → Markdown file` (a name search over the worktree) and from file paths in chat. No editing.
- **Emulator**: a media stream, not JSON-RPC lines; it needs its own channel with backpressure, like the terminal stream (design §8 cross-cutting 3).

## 4. Layout: tabs and splits

**Model** (pure TypeScript in `packages/app-core/workspace.ts`, no React or DOM, so web and mobile share it and Node tests drive it):

```
Workspace  { v: 1, root: Node, active: GroupId, tabs: Record<TabId, Tab> }
Node       = Group { id, tabs: TabId[], activeTab }  |  Split { dir: 'row' | 'col', a: Node, b: Node, at: number }   // at = share of a, 0.15..0.85
Tab        { id, kind: 'chat' | 'terminal' | 'shell' | 'emulator' | 'web' | 'markdown' | 'review', params }
             params: terminal {handle}, markdown {path}, web {port | url}, emulator {device}
```

Rules, each a test:

1. A workspace holds **at most 4 groups** (leaves). A drag to a pane edge that would make a fifth is refused; dropping on a group's tab strip (merge) always works. Any shape is allowed: 1|2 rows, 2×2, one wide pane over two narrow ones.
2. **Dragging a tab onto an edge of a pane** (right, bottom, left or top) splits that pane and puts the tab in the new group. **Dropping on a tab strip** moves it there. **Dragging to a free edge** of the whole view splits the root. A layout menu offers the presets single, two columns, two rows and 2×2, which rearrange existing tabs (extra tabs merge into the last group).
3. **Chat cannot be closed**: there is no close control, and `closeTab` on it is a no-op. It can be moved to any group; the workspace has exactly one Chat tab, created with the workspace and recreated if a saved layout lacks it.
4. Closing the last tab of a group removes the group and its sibling takes the space. A tab close only detaches; it never stops an agent, an emulator or a terminal (design §3.2).
5. Divider drag sets `at`, clamped; double-click resets to 0.5. Min pane size 320×200 CSS px: a window too small for the saved splits **renders the groups as one tab group** (the saved layout is untouched, the stored order is the tab order). Below the phone breakpoint (640) there is always one group and no drag; the web then behaves like the mobile app (design §7).
6. Opening the same thing twice (the same file, the same terminal handle) focuses its tab instead of adding one.
7. Closing an agent session tab leaves a way back: `＋ → Terminals` lists the agent's terminals (R2) and `＋ → Claude Code` reattaches.

Keyboard: arrow-key move of a tab between groups and Escape-to-cancel a drag, so splitting is possible without a pointer. Touch drag is not built (phones use one group).

**Renderer.** The model is ours and library-free. Unit 3 may drive a docking library (dockview is the candidate) from it, but only if the model stays the source of truth and the rules above hold;
otherwise it builds the strip and dividers itself.

**Where the layout is saved: in the browser, per agent, per daemon** (`localStorage`, key `handline.workspace.v1/<daemon id>/<agent>`), as the design already says. Why not in the daemon:
the layout is how one screen shows things, and a laptop's 2×2 is wrong on a phone; no API is needed; the Electron wrapper uses the same storage. Saved: the tree, split shares, tab list with params, active tabs.
Not saved: scroll, terminal text, input holds. On load it is validated against the schema; a tab whose agent, terminal or file is gone stays visible in an error state until closed, never dropped silently.
Cost: layouts do not follow a person from laptop to another browser. If that is wanted, the later step is one daemon record per device (`layout.get/set`), the same JSON; nothing in the model changes.

## 5. Tests the builders owe

`workspace.ts`: the seven rules above, a save/load round trip, a layout from a newer `v` is ignored with the default, 4-pane cap on drag and on presets, Chat survives every sequence of moves and closes (a property test over random operations).
Web: DOM test that closing a Chat tab is impossible, and a screenshot at 375, 768 and 1280 px with two and four panes.

## 6. Requests to the daemon (orcapipe)

None blocks unit 3; they gate the tab kinds in §3. Each is a new method or field in `contract/protocol.json`, with its own gate and role like the others.

| # | Request | Shape |
|---|---|---|
| R1 | Vendor of an agent's session | `vendor` (`claude`, `codex`, open set) on `AgentView` or `AgentDetail`; the UI labels the tab from it |
| R2 | Terminals of an agent | `AgentDetail.terminals: [{ handle, kind: 'session' \| 'shell' \| 'login', title }]`; `terminal.open { agent, kind: 'shell' }` returns a handle for `terminal.attach` (`web/README.md` lists the shell pane as waiting on shell terminals) |
| R3 | Emulators | `emulator.list`, `emulator.start` / `stop { device }`, a screen channel (JPEG frames over a binary WebSocket, rate-limited, one producer per device) and `emulator.input { tap, swipe, text, key }`; capability `emulator`. The host is the daemon's machine, so it also needs the SDK tools there |
| R4 | Web proxy | `web.ports { agent }` lists listening ports of the worktree's processes; `web.open { agent, port }` returns a path on the daemon's own TLS port that proxies to it, role-checked and closed when the last viewer leaves. Without it a remote viewer cannot reach `localhost:3000` on the host |
| R5 | Files | `worktree.file { agent, path }` (text, size limit, path confined to the worktree, symlinks resolved first), `worktree.files { agent, query }` for the picker, and a `file.changed` notice on a topic or an `etag` for cheap polling |

R4 and R5 widen what a viewer can read on the host beyond the terminal, so the daemon team decides roles and confinement (design §8 cross-cutting 7); the UI assumes member-or-founder and greys the tab otherwise.

## 7. Units 3 and 4

Units 2, 3 and 4a–4e of `docs/turborepo-layout.md` §6 are written to match this document: unit 2 adds `app-core/workspace.ts` with the tests of §5; unit 3 builds the sidebar of §2,
the tab strip and the split panes with the Chat and Terminal tabs on today's API; unit 4a makes them responsive and removes `web/ui` and `web/mobile`;
4b–4e add the Shell, Markdown, Web and Emulator tabs as R2, R5, R4 and R3 land, and the Claude Code / Codex label waits on R1.
The mobile app (unit 5) is unaffected: it shows Chat and Terminal as two screens and never splits.

## 8. Assumptions to confirm

- **One Chat per agent**, in that agent's workspace, not one global Handline chat. If the founder means a single global chat, it becomes a fixed tab present in every workspace.
- "Top" means the agent page header; the app's top bar (search, Inbox, Tasks, Team, Accounts; design §2) stays. Inbox, Tasks and the other destinations stay routes that replace the main view, not tabs.
- Reversing F26 and F28 (browser and emulator) is intended.
