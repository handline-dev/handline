# Terminal pane: xterm.js on the `bytes` terminal channel

The Terminal tab of `docs/web-main-view.md` §3 shows an agent's session as a text grid today. The daemon team is adding format `bytes` to the terminal
channel (daemon team, tmux host): the window's output as a byte stream, a snapshot with its escape codes on attach, keys in, resize. This plans our side:
an **xterm.js pane** that renders the real Claude Code TUI, with colour, cursor and the dialogs as they are on screen.

Founder: terminals inside the IDE; the bridge keeps working until web, Electron and the app attach to the right place.

**Status.** Written against `contract/` on main @7cdaeab1 and the daemon team's brief for `bytes`. Its spec and fixtures have not landed (the sha is asked for).
Everything that depends on them is marked **[bytes spec]** and collected in §9; each is checked against the fixtures before its unit starts.

## 1. What changes, what stays

| Stays | Changes |
|---|---|
| `terminal.attach` / `history` / `resize` / `detach`, `input.take` / `release`, the `holder` and `marker` notices, `session.*`, `prompt.answer` | the pane renders **bytes** in xterm.js when the daemon offers them |
| The text-grid pane (`web/ui/terminal-pane.ts`, later the same view in `apps/web`) | it becomes the **fallback**: Orca host, a daemon without `bytes`, the phone app (no xterm in React Native), and a setting "Terminal: text grid" |
| Parsed facts drive banners, badges and cards (design §4.1) | the pane never reads dialogs or prompts out of the bytes |
| The bridge's chat page and its terminal view | untouched; nothing here edits the bridge |

The styled-rows decision of `web-ui-design.md` §9 ("colour is the founder's, pending") is answered for this path by the byte stream: colour comes from the host's own escape codes. The text-grid path stays uncoloured.

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

1. **Attach** (tab rendered and visible): choose the format (§8), call `terminal.attach {agent, format: 'bytes', history}` **[bytes spec: how format is asked and offered]**. The reply is the snapshot: the screen with escape codes, `cols × rows`, cursor, `seq`, holders.
2. **Paint the snapshot.** `term.reset()`, `term.resize(cols, rows)`, write the snapshot, move the cursor to `cursor`. The snapshot is written unchanged, so the spec must make it xterm-ready (line endings as `\r\n`, cursor; §9.3). It is written in **one** `write` so a frame never renders between reset and paint.
3. **Stream.** Each notice carries `seq`. `seq ≤ snapshot.seq` is dropped (it is in the snapshot), `seq = last+1` is written, a hole means **re-attach and repaint**. The same rule the text view has (`fits(seq)`), so the daemon's "slow client gets a full frame instead of a backlog" stays the daemon's job; bytes cannot be dropped, so a slow client is re-snapshotted **[bytes spec: the daemon's rule for a slow reader]**.
4. **Client backpressure.** `term.write(data, cb)` counts bytes in flight; over 1 MB unprocessed, drop the queue and re-attach. 
5. **Detach** (tab hidden, closed, agent not selected, browser tab hidden, page unload): `terminal.detach` at once, and it **releases the input if this device holds it** (§5), so the hold never outlives the attach. The xterm instance is kept 30 s (a quick switch back shows the last screen, then repaints from the new snapshot) and disposed after. Detach never stops the agent.
6. **Reconnect.** The transport reconnects by itself (`packages/client`, cursor resume for events). The view goes `stale`: the screen stays, dimmed, with "Reconnecting…" and input off; on `open` it attaches again and **repaints from the new snapshot**, so nothing missed while offline is replayed or guessed. A snapshot that arrives is atomic (point 2); a failed attach (`not_found`: terminal closed, `refused`) shows the reason in place of the grid with a Retry.
7. **Terminal gone while attached** (session closed, terminal closed): the daemon's state change reaches the pane through `agent.changed`; the pane shows the last screen dimmed with "No terminal" and does not retry until the agent has one.

**Scrollback.** xterm holds the scrollback (`scrollback: 5000`). Attach asks for `history` lines (2000) and writes them above the screen; older lines page in with `terminal.history` (absolute index, `before` / `limit`), written above what is held; xterm cannot prepend, so "Load older" repaints with the pages joined, keeping the scroll anchor by absolute line index. The tmux history is escape-coded or plain **[bytes spec: history lines under `bytes`]**; plain history renders uncoloured and is acceptable in v1.

**Alternate screen.** A full-screen program in a shell tab (vim, less) switches the screen buffer; a snapshot of the alternate screen needs the flag **[bytes spec: alt-screen in the snapshot]**. The Claude Code session does not use it.

## 4. Resize

- The terminal has one size, `cols × rows`, owned by the host and by the **input holder** (design §4.2; `terminal.resize` is accepted from the holder only and restored when they leave).
- **Not holding:** the pane never asks for a size. It follows the host's `resize` notices (`term.resize`) and fits the grid in the pane by **scaling the font** (floor 9 px; below that the grid scrolls sideways). A phone and a laptop watching one agent therefore both see the real grid.
- **Holding:** on take, and on every pane size change after, `FitAddon.proposeDimensions()` → `terminal.resize {cols, rows}` after 150 ms of no change; never while the pane has no size (hidden, 0×0); clamp to 20..400 × 5..200 or what the daemon reports. On release the daemon restores, and the `resize` notice repaints.
- Resize does not reflow in xterm the way tmux does; after any size change the pane takes the **snapshot** for the new size rather than trusting local reflow.
- Dragging a divider (web-main-view §4) fires the size change only on release, not on each move.

## 5. Input and the holder

The pane is **read-only until the person takes the keyboard** (design §4.3). One holder per session in v1, the model a list.

States of the terminal tab's strip (it carries these controls, web-main-view §2):

| Holder | Strip shows | Pane |
|---|---|---|
| `daemon` | "Daemon types here" and **Take control** | read-only; a click or first key shows a one-line confirm "Take control? The daemon pauses delivering tasks and keys" |
| this device | "input: YOU 3:12" (time since last key against `input_hold_idle_s`, from settings), **Release** | typing live; the idle clock restarts on each key |
| another person or device | "held by <name> since <time>"; **Ask** only if `capabilities.input_ask` | read-only |
| unknown / stale | "Reconnecting" | read-only |

- **Take** = `input.take` first, always (§4.3); the pane does not rely on a short hold. Keys typed before the reply are discarded, not queued.
- **Release** = Release button, any detach (§3.5), or the idle timeout (the daemon's `holder` notice is the truth; the strip follows it, never its own timer alone). Taking back after an idle release asks again.
- **While held, the daemon's own typing stops** (queued work shows "held" in Tasks, design §4.3). The strip says so; it also says how many tasks wait.
- **What is sent.** xterm's `onData` gives the bytes the TUI expects (arrows as `ESC [ A`, Ctrl-C as `0x03`, bracketed paste wrapped). Sent as raw input on the terminal, translated by the daemon to `send-keys` **[bytes spec: the method and encoding, `terminal.input` or reuse of `session.keys`/`session.input`]**. A raw input method is a precondition of P3: `session.keys` has no letters or left/right, so it cannot drive a TUI and is not a fallback.
- **Dialog gate [bytes spec].** `session.input` is refused while a dialog is open (design §9). In the pane the person is *in* the TUI and may need to answer the dialog by hand. The plan assumes terminal input is exempt from the dialog gate because the holder rule and the explicit take cover it; if not, the pane's strip says "Dialog open: answer in Chat" with a button to the card.
- **Key chips** (Esc, ^C, ↵, arrows, Tab, ⇧Tab, 1 2 3) stay in the strip, take the keyboard first like typing, and use the same send path as `onData`.
- **The daemon's own typing** is not written into the stream (it would corrupt the TUI's state). The `marker` notice becomes an xterm **decoration** on the row (`registerMarker` + `registerDecoration`): a faint tick in the gutter, tooltip "daemon typed: <text>", and a line in the agent's Timeline. Person-typed control events likewise.
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
- Input is subject to the daemon's role gates; the UI greys the tab for a viewer who may not take **[role of `bytes` input, as for `terminal.resize`]**.

## 8. Choosing the renderer

`ByteTerminalView` is used when **all** hold: the daemon's `hello` says it serves `bytes` **[bytes spec: capability name]**; the setting is not "text grid"; the viewer is not below the phone breakpoint with the keyboard strip as the primary input (portrait: text view with the key strip, as design §7; landscape and tablet: xterm). Otherwise the text-grid view. Switching renderers is a re-attach; no state carries across. The daemon is asked for one format per attach, so two clients on one terminal may use different formats.

## 9. Assumptions for the bytes spec

Each is a question to orcapipe; the answer is in the fixtures or the doc is corrected.

1. How a client asks for and learns `bytes` (`terminal.attach` param and a `hello` capability).
2. The raw-input method and its encoding, and whether it is exempt from the dialog gate (§5).
3. **Snapshot and stream ordering**: does the snapshot's `seq` mean "frames with `seq` above this follow", with no gap and no duplicate between `capture-pane` and the first `%output`? And are the snapshot's bytes xterm-ready (`\r\n` line endings, not the `\n` of capture-pane), so the pane writes them unchanged?
4. Frame encoding on the wire: base64 in JSON, or a binary WebSocket frame? (Bytes are not valid UTF-8 in general.)
5. The slow-reader rule: is a full snapshot pushed, or does the client re-attach?
6. History under `bytes`: escape-coded or plain; how many lines.
7. Alternate-screen flag and the cursor shape/visibility in the snapshot.
8. Size on attach for a viewer who does not hold; whether `resize` notices carry the tmux window size after `resize-window` restores.
9. `marker` stays a notice beside the stream (assumed) rather than bytes in it.
10. `input_hold_idle_s` is marked new in the contract: the idle countdown (§5) needs the daemon to implement it.
11. Fixture for the reconnect from a snapshot (the brief promises one), and one with a hole in `seq`.

## 10. Tests

- **Contract.** `ByteTerminalView` against the bytes spec's fixtures through `fake-daemon`: attach, in-order stream, duplicate and hole in `seq`, reconnect from a snapshot, resize notice, holder notices, slow-reader case.
- **Screen equality.** With `@xterm/headless`: snapshot + stream written into a headless terminal equals a fresh attach's snapshot (the fixtures give both). This is the test that catches stair-stepping, lost cursor and bad reflow.
- **Adapter.** Queries leave `onData` silent; OSC 52 does nothing; a title sequence does not rename the tab; a link needs its confirm; `onData` from typing reaches the send path only while holding; discard before the take reply.
- **Holder UI.** DOM tests for each row of the §5 table, the idle countdown following the daemon's notice, release on tab close.
- **Badges.** Table test from `AgentView` samples to badges, including an unknown state.
- **Screenshots** at 375, 768 and 1280 px, a coloured Claude Code screen, a dialog and the stalled state; run once against the real tmux daemon when the bytes implementation is on main, and the result written in the unit's note.

## 11. Work units

Numbered **P1..P5** to keep clear of turborepo-layout's units. All come after unit 3 (`apps/web` shell with Chat and Terminal tabs on the text grid); P1 touches only `packages/` and may run beside 4a. The first column is a suggestion; the manager assigns. Each goes through the auditor, and its note gives the test count before and after.

| # | Unit | Suggested owner | After | Done when |
|---|---|---|---|---|
| P1 | `ByteTerminalView` and the bytes format in `fake-daemon`, from the bytes spec's fixtures | term-build (wrote the client) | unit 3, bytes spec on main | the contract and screen-equality tests of §10 pass with no DOM; the text view's 17 client tests unchanged |
| P2 | xterm pane, read-only: attach, detach, reconnect, scrollback, fallback to the text grid (§3, §8, §7), theme from tokens | pc-build | P1, unit 3; the phone-width rule and its screenshots land with 4a's responsive collapse | a live colour screen from the fake daemon in the browser; reconnect repaints; the adapter safety tests pass |
| P3 | Input and holder: take / release / idle clock, send path, key chips, daemon markers, resize ownership (§4, §5) | pc-build | P2 | the holder table and send-path tests pass; resize verified at two pane sizes; typing reaches the fake daemon only while holding |
| P4 | Badges and the tab per agent on the tab and the sidebar row (§6) | pc-build | P2 (parallel to P3) | the badge table test; the unread dot clears on show; screenshots with a dialog and a stalled agent |
| P5 | Live check on the real tmux host and the notes it forces | term-design (this role) or pc-build | P3, P4, the Python daemon with `bytes` on main | screenshots and a recorded reconnect against a real session, Claude Code TUI usable (dialog answered by hand in the pane); §9 answers folded into this doc |

## 12. Not in scope

Phone app terminal (text view and key strip stay, no xterm in RN); several people typing at once (the list model is kept, §5); WebGL renderer; a shell tab (needs R2 of web-main-view; the same pane serves it when the daemon opens one); recording or replaying sessions; any change to the bridge.
