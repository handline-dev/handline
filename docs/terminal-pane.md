# Terminal pane: xterm.js on the `bytes` terminal channel

The Terminal tab of `docs/web-main-view.md` §3 shows an agent's session as a text grid today. The daemon team is adding format `bytes` to the terminal
channel (daemon team, tmux host): the window's output as a byte stream, a snapshot with its escape codes on attach, keys in, resize. This plans our side:
an **xterm.js pane** that renders the real Claude Code TUI, with colour, cursor and the dialogs as they are on screen.

Founder: terminals inside the IDE; the bridge keeps working until web, Electron and the app attach to the right place.

**Status.** The bytes spec is on main: `contract/protocol.md`, "Terminal channel" and "Format bytes" (generated from `contract/protocol.json`). §9 records its answers to our questions; the places they changed this doc say "(spec)".

## 1. What changes, what stays

| Stays | Changes |
|---|---|
| `terminal.attach` / `history` / `resize` / `detach`, `input.take` / `release`, the `holder` and `marker` notices, `session.*`, `prompt.answer` | the pane renders **bytes** in xterm.js when the daemon offers them |
| The text-grid pane (`web/ui/terminal-pane.ts`, later the same view in `apps/web`) | it becomes the **fallback**: Orca host, a daemon without `bytes`, the phone app (no xterm in React Native), and a setting "Terminal: text grid" |
| Parsed facts drive banners, badges and cards (design §4.1) | the pane never reads dialogs or prompts out of the bytes |
| The bridge's chat page and its terminal view | untouched; nothing here edits the bridge |

The styled-rows decision of `web-ui-design.md` §9 ("colour is the founder's, pending") is answered for this path by the byte stream: colour comes from the host's own escape codes. The text-grid path gets colour from the `styled` format (spec: `contract/protocol.md` "Format styled"): the same rows and frames as text, each row with style runs (`spans`). It is for a host that has no byte stream (Orca, once its screen read exposes cell attributes, which is an open request to Orca) and for the phone, which has no xterm. A host that lists neither `bytes` nor `styled` stays plain text.

## 2. Where the code lives

xterm.js is the first dependency of a terminal view, and `packages/client`, `app-core` and `fake-daemon` stay dependency-free (turborepo-layout §7).

| Piece | Package | Depends on |
|---|---|---|
| `ByteTerminalView`: attach state machine, seq checks, snapshot, reconnect, input queue, holder state. Emits `reset(snapshotBytes)`, `write(bytes)`, `resize(cols, rows)`, `marker`, `holders` | `packages/client` | nothing |
| Bytes format in the fake daemon, replaying the bytes spec's fixtures | `packages/fake-daemon` | nothing |
| xterm adapter: `Terminal` instance, fit, theme from `packages/tokens`, link handling, the safety handlers of §7 | `apps/web/src/terminal/` | `@xterm/xterm`, `@xterm/addon-fit`, `@xterm/addon-web-links` |
| Terminal tab chrome: holder strip, badges, key chips | `apps/web` (screens are written per app); the holder state machine and badge derivation are pure code in `packages/app-core`, Node-tested | `app-core` |
| Tests that run xterm without a DOM | `@xterm/headless` as a dev dependency of `apps/web` | |

Not used: the WebGL addon in v1 (context loss, GPU variance; the DOM and canvas renderers are enough for ~5 panes); the serialize addon (the daemon's snapshot is the source of truth).

## 3. Attach, detach, reconnect

`ByteTerminalView` states: `detached → attaching → live → stale (transport down) → attaching …`.

1. **Attach** (tab rendered and visible): choose the format (§8), call `terminal.attach {agent, format: 'bytes', history}`. The reply is the snapshot: `data` (base64), `cols × rows`, `alt_screen`, `cursor`, `cursor_visible`, `seq`, `history`, `holders`. A host that cannot serve bytes refuses with gate `unsupported` and attaches nothing; the pane then asks again with `text` (§8).
2. **Paint the snapshot.** `term.reset()`, `term.resize(cols, rows)`, write the decoded `data`, in one step. `data` is xterm-ready (spec): it starts with `ESC [ ? 1049 h` when `alt_screen`, paints the rows with CR LF, sets the modes (application cursor keys, bracketed paste, mouse), and ends by placing and showing or hiding the cursor, so the pane moves nothing itself. It is written in **one** `write` so a frame never renders between reset and paint. The same paint serves a `snapshot` notice (point 3).
3. **Stream.** Notices are `output` (base64 bytes; one may end inside a character or an escape sequence, so they are written as bytes to xterm, which keeps the parser state), `snapshot`, `marker` and `holder`, all in one `seq` counter that goes up by 1 (spec). `seq ≤ last applied` is dropped, `seq = last+1` is applied, a hole means **re-attach and repaint**. The snapshot's `seq` is the last notice its screen includes, so the first notice after the reply is `seq+1` with nothing lost or repeated. **Slow client:** when 1 MiB of output is unsent the daemon drops it all and sends a `snapshot` notice, reason `slow_client`; the pane repaints from it (point 2) and does not re-attach. If the connection itself falls behind the event gap rule, it is closed and point 6 applies.
4. **Client backpressure.** `term.write(data, cb)` counts bytes in flight; over 1 MB unprocessed, drop the queue and re-attach. 
5. **Detach** (tab hidden, closed, agent not selected, browser tab hidden, page unload): `terminal.detach` at once, and it **releases the input if this device holds it** (§5), so the hold never outlives the attach. The xterm instance is kept 30 s (a quick switch back shows the last screen, then repaints from the new snapshot) and disposed after. Detach never stops the agent.
6. **Reconnect.** The transport reconnects by itself (`packages/client`, cursor resume for events). The view goes `stale`: the screen stays, dimmed, with "Reconnecting…" and input off; on `open` it attaches again and **repaints from the new snapshot**, so nothing missed while offline is replayed or guessed. A snapshot that arrives is atomic (point 2); a failed attach (`not_found`: terminal closed, `refused`) shows the reason in place of the grid with a Retry.
7. **Terminal gone while attached** (session closed, terminal closed): the daemon's state change reaches the pane through `agent.changed`; the pane shows the last screen dimmed with "No terminal" and does not retry until the agent has one.

**Scrollback.** xterm holds the scrollback (`scrollback: 5000`). Attach asks for `history` lines (default 100, at most 2000) and writes them above the screen; older lines page in with `terminal.history` (absolute index, `before` / `limit`, `more` says whether older exist, a page is trimmed to 1 MiB), written above what is held; xterm cannot prepend, so "Load older" repaints with the pages joined, keeping the scroll anchor by absolute line index. History lines are strings with their SGR codes (spec): one physical row each, starting at default attributes and ending in a reset, no line break inside, so the pane joins them with CR LF and the colour is kept.

**Alternate screen.** A full-screen program in a shell tab (vim, less) switches the screen buffer; the snapshot carries `alt_screen` and its `data` switches to it (spec); later switches are in the stream. While `alt_screen` is true the snapshot's history is empty: only the normal screen has scrollback, so "Load older" is off then. The Claude Code session does not use it.

## 4. Resize

- The terminal has one size, `cols × rows`, owned by the host and by the **input holder** (design §4.2; `terminal.resize` is accepted from the holder only and restored when they leave).
- **Not holding:** the pane never asks for a size. It follows the size of each `snapshot` notice with reason `resize` (a bytes attach gets no `resize` notice, spec) and fits the grid in the pane by **scaling the font** (floor 9 px; below that the grid scrolls sideways). A phone and a laptop watching one agent therefore both see the real grid.
- **Holding:** on take, and on every pane size change after, `FitAddon.proposeDimensions()` → `terminal.resize {cols, rows}` after 150 ms of no change; never while the pane has no size (hidden, 0×0); clamp to 20..400 × 5..200 or what the daemon reports. On release the daemon restores, and the `resize` snapshot notice repaints. A size the daemon refuses (`cols` 5 is `bad_request` naming the field) leaves the pane at its current size.
- Resize does not reflow in xterm the way tmux does; the daemon sends a `snapshot` notice, reason `resize`, with the new `cols` and `rows` for any size change, the holder's or the restore, and the pane repaints from it (§3.2) rather than reflowing.
- Dragging a divider (web-main-view §4) fires the size change only on release, not on each move.

## 5. Input and the holder

The pane is **read-only until the person takes the keyboard** (design §4.3). One holder per session in v1, the model a list.

States of the terminal tab's strip (it carries these controls, web-main-view §2):

| Holder | Strip shows | Pane |
|---|---|---|
| `daemon` | "Daemon types here" and **Take control** | read-only; a click or first key shows a one-line confirm "Take control? The daemon pauses delivering tasks and keys" |
| this device | "input: YOU 3:12" (time since last key against `input_hold_idle_s`, from settings), **Release** | typing live; the idle clock restarts on each key |
| another person or device | "held by <name> since <time>" | read-only |
| unknown / stale | "Reconnecting" | read-only |

- **Take** = `input.take` first, always (§4.3); the pane does not rely on a short hold. Keys typed before the reply are discarded, not queued.
- **Release** = Release button, any detach (§3.5), or the idle timeout (the daemon's `holder` notice is the truth; the strip follows it, never its own timer alone). Taking back after an idle release asks again.
- **While held, the daemon's own typing stops** (queued work shows "held" in Tasks, design §4.3). The strip says so; it also says how many tasks wait.
- **What is sent.** xterm's `onData` gives the bytes the TUI expects (arrows as `ESC [ A`, Ctrl-C as `0x03`, bracketed paste wrapped). Sent with `terminal.input {terminal, data (base64), request_id}` (spec), from the input holder only. Calls on one connection apply in the order read, so the pane sends each `onData` as it comes. It is recorded as an event of the agent with the byte count, never the bytes. `session.keys` has no letters or left/right, so it cannot drive a TUI and is not a fallback.
- **Gates (spec).** `terminal.input` passes the usage wall and an open dialog, as `session.type` does, because the person sees the screen; `session.input` keeps both gates. It is refused while the daemon holds the input (take first, the `daemon` row of the table), for a terminal with no holder and for a login terminal, and by a host that cannot send keys (gate `unsupported`). So the pane needs no "answer in Chat" fallback; a refusal shows its gate in the strip.
- **Key chips** (Esc, ^C, ↵, arrows, Tab, ⇧Tab, 1 2 3) stay in the strip, take the keyboard first like typing, and use the same send path as `onData`.
- **The daemon's own typing** is not written into the stream (it would corrupt the TUI's state). The `marker` notice (same `seq` counter, standing before the output that echoes the typing, spec) becomes an xterm **decoration** on the row (`registerMarker` + `registerDecoration`): a faint tick in the gutter, tooltip "daemon typed: <text>", and a line in the agent's Timeline. Person-typed control events likewise.
- **Focus.** Tab belongs to the TUI while holding. `attachCustomKeyEventHandler` lets the workspace's pane-cycling shortcut and `Ctrl+Shift+T` (release and leave) through; not holding, Tab moves focus out as in any page.
- **Paste** goes through xterm (bracketed paste preserved); a paste over 4 KB asks to confirm. **Copy**: selection copies on Ctrl/Cmd+C when text is selected, else Ctrl-C is the interrupt; "copy on select" is the copy-on-select setting (web-ui-design §6, F17). Shift-drag selects while the TUI has mouse reporting on.
- Mouse reporting from the TUI is passed through only while holding.

## 6. A tab per agent, with badges

web-main-view fixes the layout: each agent has a **workspace** (sidebar picks it), and a workspace has one Terminal tab for the session, so there is a terminal tab **per agent**, in the pane the person put it. There is no extra per-agent tab row. A person who wants all agents on one screen uses the sidebar rail, which carries the same badges.

Badges come from the daemon's parsed facts (`AgentView`), never from the bytes, and appear on the tab label and the sidebar row:

| Badge | When | Action |
|---|---|---|
| amber `!` **dialog** | `has_dialog` or an open prompt | click focuses the card in Chat; the pane shows a one-line bar with the dialog's title |
| red **STALLED** / blocked | `state` is `stalled` or `blocked`, or `block` set (kind and since in the tooltip: no terminal, no session, host down) | click opens the Timeline tab at the cause |
| blue **YOU** | this device holds the input | click selects the pane |
| grey **held: <name>** | another holder | |
| dashed **no terminal** | `AgentDetail.terminal` is null | Start / Resume are in the sidebar, not here |
| unread dot | a hidden terminal tab whose agent just raised a dialog or stalled | clears when the tab is shown |

States unknown to this client are shown as plain text on the tab's tooltip (open set).

## 7. Safety: the bytes are foreign

Everything the host sends is the agent's output. xterm.js is configured for that:

- **No clipboard writes** from the stream (no OSC 52 handler). **Title sequences** (OSC 0/1/2) never rename the tab; they are shown, sanitized and capped at 80 characters, in the tooltip only.
- **Queries are not answered by us.** xterm replies to DA, DSR/CPR and colour queries through `onData`; those replies would arrive in the agent as keystrokes, and tmux already answers its application. The adapter registers CSI/OSC handlers for those queries that consume them, and a test asserts `onData` stays silent for a stream full of queries.
- **Links.** `addon-web-links` for `http(s)` only; OSC 8 links likewise; a click opens with `noopener,noreferrer` after a confirm that shows the real URL when it differs from the text. No `file:` or `javascript:`.
- Stream bytes are written as bytes (`Uint8Array`), never put in the DOM as markup; the adapter has no `innerHTML`.
- The screen-reader mode of xterm is a setting; the live region is off by default because a TUI redraws constantly.
- Input is subject to the daemon's role gates; the UI greys the tab for a viewer who may not take: `terminal.input` has the gates `role` (an agent session is refused), `terminal_lock` and `input_holder`, as `terminal.resize` has `role` and `input_holder`.

## 8. Choosing the renderer

`ByteTerminalView` is used when **all** hold: `hello.capabilities.terminal_format` lists `bytes` (spec; `terminal_hosts` maps each host to its formats, e.g. `{tmux: [text, bytes], orca: [text]}`, so the pane also reads the agent's host there); the setting is not "text grid"; the viewer is not below the phone breakpoint with the keyboard strip as the primary input (portrait: text view with the key strip, as design §7; landscape and tablet: xterm). Otherwise the text-grid view, which asks for `styled` when the agent's host lists it and draws each row's `spans` (colour index 0-15 from the theme tokens, 16-255 and `#rrggbb` as given, `inverse` by swapping what it draws), and asks for `text` when it does not or the host refuses (`unsupported`). Switching renderers is a re-attach; no state carries across. The format belongs to the attach, so two clients on one terminal may use different formats. A bytes attach the host refuses (`unsupported`) falls back to a text attach in the same step.

## 9. What the bytes spec answered

Questions asked of orcapipe, answered in `contract/protocol.md` ("Terminal channel", "Format bytes").

| # | Question | Answer | Where used |
|---|---|---|---|
| 1 | How a client asks for and learns `bytes` | `format` param on `terminal.attach` and `terminal.history`; `hello.capabilities.terminal_format` and `terminal_hosts`; refused with gate `unsupported` otherwise | §3.1, §8 |
| 2 | Raw input method, dialog gate | `terminal.input`, base64 `data`; passes wall and dialog; refused while the daemon holds the input | §5 |
| 3 | Snapshot and stream ordering, xterm-ready bytes | snapshot `seq` is the last notice included, next is `seq+1`; CR LF rows, modes and cursor in `data` | §3.2, §3.3 |
| 4 | Wire encoding | base64 in JSON, no binary frames | §3.3 |
| 5 | Slow reader | at 1 MiB unsent the daemon sends a `snapshot` notice, reason `slow_client`; no re-attach | §3.3 |
| 6 | History under `bytes` | SGR-coded physical rows, default 100, at most 2000, page ≤ 1 MiB with `more` | §3 Scrollback |
| 7 | Alternate screen, cursor | `alt_screen`, `cursor`, `cursor_visible` in the snapshot; no history on the alternate screen | §3 |
| 8 | Size on attach, resize notices | snapshot carries `cols`, `rows`; a size change is a `snapshot` notice, reason `resize`; no `resize` notice | §4 |
| 9 | `marker` | a notice in the one counter, before the output that echoes | §5 |
| 10 | `input_hold_idle_s` | the holder who has not typed for it loses the hold; the `holder` notice says so | §5 |
| 11 | Fixtures | to be checked against `contract/fixtures` when P1 starts: reconnect from a snapshot, a hole in `seq` | §10 |

## 10. Tests

- **Contract.** `ByteTerminalView` against the bytes fixtures (§9.11) through `fake-daemon`: attach, in-order stream, duplicate and hole in `seq`, reconnect from a snapshot, resize notice, holder notices, the `slow_client` snapshot notice and the `resize` snapshot notice.
- **Screen equality.** With `@xterm/headless`: snapshot + stream written into a headless terminal equals a fresh attach's snapshot (the fixtures give both). This is the test that catches stair-stepping, lost cursor and bad reflow.
- **Adapter.** Queries leave `onData` silent; OSC 52 does nothing; a title sequence does not rename the tab; a link needs its confirm; `onData` from typing reaches the send path only while holding; discard before the take reply.
- **Holder UI.** DOM tests for each row of the §5 table, the idle countdown following the daemon's notice, release on tab close.
- **Badges.** Table test from `AgentView` samples to badges, including an unknown state.
- **Screenshots** at 375, 768 and 1280 px, a coloured Claude Code screen, a dialog and the stalled state; run once against the real tmux daemon when the bytes implementation is on main, and the result written in the unit's note.

## 11. Work units

Numbered **P1..P5** to keep clear of turborepo-layout's units. All come after unit 3 (`apps/web` shell with Chat and Terminal tabs on the text grid); P1 touches only `packages/` and may run beside 4a. The first column is a suggestion; the manager assigns. Each goes through the auditor, and its note gives the test count before and after.

| # | Unit | Suggested owner | After | Done when |
|---|---|---|---|---|
| P1 | `ByteTerminalView` and the bytes format in `fake-daemon`, from the bytes fixtures | term-build (wrote the client) | unit 3 | the contract and screen-equality tests of §10 pass with no DOM; the text view's 17 client tests unchanged |
| P2 | xterm pane, read-only: attach, detach, reconnect, scrollback, fallback to the text grid (§3, §8, §7), theme from tokens | pc-build | P1, unit 3; the phone-width rule and its screenshots land with 4a's responsive collapse | a live colour screen from the fake daemon in the browser; reconnect repaints; the adapter safety tests pass |
| P3 | Input and holder: take / release / idle clock, send path, key chips, daemon markers, resize ownership (§4, §5) | pc-build | P2 | the holder table and send-path tests pass; resize verified at two pane sizes; typing reaches the fake daemon only while holding |
| P4 | Badges and the tab per agent on the tab and the sidebar row (§6) | pc-build | P2 (parallel to P3) | the badge table test; the unread dot clears on show; screenshots with a dialog and a stalled agent |
| P5 | Live check on the real tmux host and the notes it forces | term-design (this role) or pc-build | P3, P4, the Python daemon with `bytes` on main | screenshots and a recorded reconnect against a real session, Claude Code TUI usable (dialog answered by hand in the pane); notes the live check forces folded into this doc |

## 12. Not in scope

Phone app terminal (text view and key strip stay, no xterm in RN); several people typing at once (the list model is kept, §5); WebGL renderer; a shell tab (needs R2 of web-main-view; the same pane serves it when the daemon opens one); recording or replaying sessions; any change to the bridge.
