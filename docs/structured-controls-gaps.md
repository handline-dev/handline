# Structured agents in the apps: what is missing

Read against main 45d76bb6 and the served Go API. A structured agent has no terminal: it is controlled with `session.*` (start, wake,
restart, stop, park, interrupt), `chat.*` (send, send_now, cancel, answer) and `prompt.answer`, and watched through `transcript.events`
and the `transcript` topic. It must never be sent `terminal.*`, `session.input`, `session.keys`, `session.type`, `input.take` or
`input.release`. The Go daemon does not serve those five either (`go/conformance-missing.txt`).

Already right on main: chat receipts, Send now, Take back and the unknown note (web and phone, `packages/app-data/src/chat*.ts`); the
transcript view and its permission cards; `delivery` on a send; interrupt as a control request; the `structured` flag choosing
Transcript over Terminal on the phone and in the web Watch tab.

## Contract and fixtures (agree with the daemon first)

| # | Gap | Proposal |
|---|---|---|
| C1 | `agent.get` sets `session_open` from `terminal != ""` (`go/internal/reads/agents.go:143`). A structured row has no terminal, so a live session reads `session_open=false`. | Agreed with the daemon: for `structured`, `session_open` = a live session; `terminal` null; `input_holders` []. Contract text done; the Go change is the daemon's. |
| C2 | Withdrawn. I read `structured` as live-session only; the daemon sets it from the agent's config (`agent.yaml`), so a closed or parked structured agent already reads `structured: true` and serves its stored log and receipts. The contract text now says so. | None. |
| C3 | Verb texts are terminal words: interrupt "(Esc)", start "in the agent's terminal", stop `exit` "asks Claude to quit" (Go closes stdin, so exit and close are the same), kill "ends the process". Gates list `terminal_lock` and `input_holder` for interrupt and `prompt.answer`, which a structured agent never meets. | Reword for structured; say `exit` equals `close`; list the gate a structured call can hit (`state`). |
| C4 | `session.start` `account` is bad_request in Go (fixture `a_named_account_is_not_served`), but the web offers the picker. | Agreed with the daemon: not landing. Apps hide the picker (W4); a structured agent is placed by the daemon and moved with `agent.move`. |
| C5 | No fixture runs the structured controls: `session.interrupt` on a structured agent and on an idle one, `session.stop` close mid-turn (exists, needs `running_agent`), `prompt.answer` with `yes`/`no`/`esc` on a structured prompt, `session.input`/`keys`/`type`/`input.take` refused for a structured agent (today: `not served`). | Add `structured_controls.json`; add the matching TS fake behaviour (F1). |
| C6 | The TS fake plays a structured agent with a terminal, `session_open` true and a holder, so no app test sees the real shape. | F1 below. |

### F1 TS fake (packages/fake-daemon)
Done when: a structured fake agent has `terminal: null`, `session_open` from its session, `input_holders: []`; `session.input`,
`session.keys`, `session.type`, `input.take` are refused for it (gate unsupported; the Go daemon does not serve them at all) and `terminal.attach` by agent is not_found; `session.interrupt` ends the running turn and is
refused (gate state) when none runs; `structured_controls.json` passes against the fake and the Go daemon.

## packages/app-core and app-data

| # | Gap | Done when |
|---|---|---|
| A1 | `controlsOf` (`app-core/src/model.ts`) derives start, resume, stop, take, release, typing from `session_open`, the holder and `terminal`. With C1 unmet or met, take/typing/release stay meaningless for a structured agent. | `controlsOf(d, me)` for `d.structured` returns `take`, `release`, `typing` false; `stop` = live session; `interrupt` = live session and state `running` or `waiting`. Unit tests for live, idle, closed and stalled. |
| A2 | `verbsOf` has no structured case: no `interrupt`, and `fold` applies to any open session. | Interrupt is a verb of its own (`VERBS`, `callOf`, `useVerbRun`, `useVerbMark`), listed only while a turn runs. `close` and `exit` collapse to one entry for a structured agent. Test table per state. |
| A3 | `STOP_MODES` hints are terminal words ("Esc", "/exit", "kills the terminal"). | `stopModesOf(a)` returns Interrupt, Close, Kill with session words for a structured agent; the terminal list is unchanged. |
| A4 | `badgesOf` adds `no_terminal` ("Start or resume the agent to open one") when `terminal === null`, true for every structured agent. | No `no_terminal` badge for `structured`; a closed one says "No session". |
| A5 | `useWrite` knows `session.input/keys/type`, `terminal.input/resize`, `input.take/release` for any agent. Nothing stops a structured agent being sent one. | `useWrite` for these methods throws in dev and test when the agent row is `structured`; a guard test lists the five. |
| A6 | `useTranscript` returns `terminal` when the read is refused, which both apps use as "show the terminal". A structured agent should never get there. | The `terminal` fallback stays for a daemon without the topic only; tests cover a refused read for `structured: true` as an error with Retry. |
| A7 | Resume is two things: `verbsOf` uses `session.wake`, the Resume buttons use `session.restart`. | One path: a closed agent with a session id is woken (`session.wake`); restart is for an open session. Check the Go `Restart` of a closed agent before choosing. |
| A8 | `Store.openAgent` (`app-core/src/store.ts`) attaches the agent's terminal before it sets the page. For a structured agent the daemon answers not_found (it has no terminal), so the phone's agent page never opens; the web opens by route and needs the terminal handle only for the Watch tab (W1). | `openAgent` for a `structured` agent sets the page with no terminal view, and the phone never builds Terminal for it. Tests run on the fake in its real shape (the `structuredTerminal` lines below are gone). |

## apps/web

| # | Gap | Done when |
|---|---|---|
| W1 | The Watch tab is only created when `detail.terminal` is set (`Workspace.tsx:89-94`, `Pane.tsx` Add-tab menu, `ChatMenus.tsx:65` `/terminal`). A structured agent has none, so there is no way to open its transcript; the menu says "No terminal: start the agent first". | A structured agent opens with Chat and Transcript. The Add-tab menu offers Transcript, the tab is labelled and iconed Transcript (not "Claude Code"), `/terminal` becomes `/transcript`. Test: fake structured agent shows the tab with no `terminal` handle. |
| W2 | Stop menu (`Sidebar.tsx`): Exit and Kill carry terminal hints; Kill confirms "Kill the terminal of …". Interrupt is listed even when no turn runs. | Menu from A3; Kill says "End the session"; Interrupt only while a turn runs. |
| W3 | Close tab confirm says "Close the Claude Code tab … keeps running"; `TermBar`, `useInputHold`, `TerminalTab`, `BytesTab` are reachable through the `terminal` fallback with Take control. | None of them render for `structured`; the fallback shows an error with Retry (A6). |
| W4 | Start offers an account picker; the daemon refuses `account` (C4). | Picker hidden until the daemon serves the field; a Start sends `{agent}` only. |
| W5 | No Interrupt next to the live view: it is in the rail's overflow menu only. | The transcript header shows "Working…" with an Interrupt button while a turn runs; a refused interrupt shows its reason. |
| W6 | `ChatTab` empty state says "Use the terminal tab to work with the agent". | Structured: "Send a message to start". |
| W7 | Close mid-turn is refused by the daemon (gate state); the web asks first then fails. | The refusal's reason is shown on the verb, and the confirm for Close of a running agent offers Interrupt first. |

## apps/mobile

| # | Gap | Done when |
|---|---|---|
| M1 | The header row (`AgentPage.tsx`) shows Interrupt, Exit, Close, Kill for any open session, all four for a structured one (Exit = Close). Resume sends `session.restart`. "Start new" starts over a stored session id. | From A2/A3: Interrupt (while a turn runs), Close, Kill; Resume = wake; "Start new" asks first. Tests per state. |
| M2 | Withdrawn with C2: `structured` does not flip when a session closes. | None. |
| M3 | `Terminal` fallback in `TranscriptTab` for a refused read shows Take control, a key strip and a composer, none of which a structured agent can use. | Error with Retry (A6). |
| M4 | A permission card is hidden on the Transcript tab when the transcript is ready (correct) but the Chat tab shows `PromptSlot` only from `detail.prompt`. | Check that a prompt opened by a structured session reaches `detail.prompt` (`optionsOf` gives Allow/Deny with `yes`/`no`); test with the fake. |
| M5 | No Interrupt beside the "running" line of the transcript. | Same as W5. |

## Done on the contract and the fake

C1, C3, C4 and C5 are in `contract/protocol.json` (`x-structured-controls`, the method texts, `unsupported` on the typing methods) with
`contract/fixtures/structured_controls.json` (11 fixtures; they need vars, so the Go run lists them in `go/conformance-needvar.txt`
until the daemon seeds them). C2 and C6 were not gaps. F1 is in `packages/fake-daemon` (`structured-controls.ts`): a structured fake
agent has no terminal or holder, refuses the typing methods and finds no terminal to attach, interrupts only a running turn and refuses
a close mid-turn. Run them with `serve.ts --conformance --autoplay 50`, vars `structured_agent=handline.auditor turn_agent=conf.working
interrupt_agent=conf.interrupt asking_agent=conf.asking`. The Python fake does not play them (`NOT_YET_IN_PYTHON`).

Until A8 and W1 land, `setup({ structuredTerminal: true })` gives the app tests the old shape: `apps/web/test/send.test.tsx`,
`apps/web/test/transcript.test.tsx` and `apps/mobile/test/harness.tsx`. Each app deletes its lines with its fix.

## Order
C1/C3/C5 with the daemon → F1 → A1–A7 → W and M in parallel. W1 needs C1 and F1 first; the rest of A only needs the
`structured` flag. `session.park` returns false from the Go daemon until the structured tick runs the handoff (before the cutover), so Park is offered and a refusal is shown with its reason (W7, M1).
