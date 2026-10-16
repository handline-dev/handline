# Story 8-0: the Go packages as standalone modules, agentctl first

Research only; no code moves. The graph is `go list -json ./internal/... ./cmd/...` on main `888462dae`,
production imports only (tests can import across modules without making a cycle). There are **70**
packages under `go/internal`, plus `cmd/handlined`, `cmd/heavyslot` and `cmd/hl`.

## 1. Package to module

Eight modules, lowest first: each may import only those above it in this order. The founder's six are
there, plus two that the graph forces. **kit** holds the leaf utilities nobody owns: 15 packages with
no internal imports, or only other kit packages. **data** holds the database and team config that
every domain reads (`db` alone is imported by 31 packages, `cmd/hl` among them).

| Module (packages) | Packages | Imports modules | Imports that break the order (to cut) |
|---|---|---|---|
| **kit** (15) | `ansi`, `atomicfile`, `gitrun`, `lock`, `paths`, `peer`, `protected`, `pyval`, `sealed`, `store`, `testkill`, `textsafe`, `timefmt`, `version`, `yamlcompat` | - | - |
| **protocol** (7) | `authority`, `delivery`, `devices`, `operations`, `pairsock`, `rpc`, `sessiontoken` | kit | - |
| **agentctl** (14) | `claude`, `claudecli`, `debugterm`, `dialogs`, `hooks`, `inputhold`, `peerinbox`, `peerport`, `prompts`, `session`, `term`, `termchan`, `transcript`, `turns` | kit, protocol, data, taskboard, scheduler | `claude`→`lifecycle`, `hooks`→`db`, `inputhold`→`db`, `prompts`→`reads`, `prompts`→`team`, `session`→`db`, `termchan`→`db` |
| **data** (7) | `attachments`, `backup`, `db`, `housekeeping`, `states`, `stream`, `team` | kit, protocol | - |
| **accounts** (4) | `subscriptions`, `usage`, `usagepoll`, `weblogin` | kit, protocol, agentctl, data | - |
| **taskboard** (10) | `adminwrites`, `chatwrites`, `fold`, `handoff`, `hrwrites`, `reads`, `supervisor`, `taskresult`, `taskwrites`, `watcher` | kit, protocol, agentctl, data, accounts, scheduler | `reads`→`lifecycle` |
| **scheduler** (5) | `agentview`, `dangling`, `lifecycle`, `schedules`, `tick` | kit, protocol, agentctl, data, accounts, taskboard | - |
| **daemon** (11) | `cmd/handlined`, `cmd/heavyslot`, `cmd/hl`, `compose`, `daemon`, `devinstance`, `fixtureplay`, `hlclient`, `legacyverbs`, `livetest`, `sessionrpc` | kit, protocol, agentctl, data, accounts, taskboard, scheduler | - |

Eight imports cross a line the wrong way. Seven are agentctl reaching into the data or the board,
which is exactly what agentctl must stop knowing (section 2). The eighth is `reads`→`lifecycle`, for
the settings values alone: `lifecycle.Settings` and its timing constants move to data.

Packages that fit no module as written, and where they land:
- `hlclient` (hl's client) reads `db` directly. It is the CLI's, so daemon glue, not protocol.
- `peerport`/`peerinbox` carry structured-session peer messages, so agentctl, not protocol.
- `delivery` (a send's mode, states and receipt) has no imports and is read by both agentctl and the
  board, so protocol.
- `subscriptions` reads usage, so accounts. `legacyverbs` is the old-verb adapter and calls
  `sessionrpc`, so daemon glue. `operations` (the in-process list of running operations that `operation.get` reads) has no imports, so it sits low, in protocol beside the method that reports it.
- `supervisor` (sending and delivery) is called by the chat and task writes, so taskboard, not
  scheduler. Its policy callers (`lifecycle`, `tick`) sit above it.
- `agentview` builds the app's agent rows from sessions, prompts and states. It is daemon-side
  composition, so scheduler, not agentctl.

## 2. agentctl

**What it is:** run one coding CLI as an agent and say what it is doing, in events.

```go
type Spec struct { Name, Dir, ConfigDir, Model, Mode string; Env []string; Driver Driver }
func Start(ctx context.Context, s Spec, sink Sink) (Session, error)
type Session interface {
    Send(text string) error           // a message, typed or by control request
    Keys(keys ...string) error        // Esc, arrows: for a terminal
    Interrupt() error                 // end the turn (control request, or Esc on a terminal)
    Stop(mode StopMode) error
    Attach(format string) (Channel, error) // text, bytes, styled
}
type Sink interface { Event(Event) } // Started, TurnStarted, TurnEnded{Reason, Background}, NeedsYou{Kind, Prompt},
                                    // PromptAnswered, TranscriptItem, Exited{Code}
type Driver interface { /* launch argv, hook install, trust and dialog screens, transcript parse: today's claude package */ }
```

**It must not know:** tasks, requests, teams and links, routing, the database, account quotas. An
account reaches it only as a config dir and env. It keeps its own small state (transcript cursor, key
presses) behind a `Store` it is handed. The daemon passes one over `db`; a test passes a map.

**It absorbs** `term`, `termchan`, `claudecli`, `session`, `turns`, `hooks`, `transcript`, `dialogs`,
`prompts` (capture and answer, not the trust policy), `inputhold`, `debugterm`, `claude`, and the peer
ports. The seven edges become:
- `session`, `termchan`, `inputhold`, `hooks`: their event rows become Sink events; the daemon writes the rows.
- `prompts`: trust and dialog rules come in as a `Policy` callback (`AutoAnswer(dialog) bool`).
- `claude`→`lifecycle`: `Driver`, `LaunchArgs` and `Hooks` move into agentctl; lifecycle imports them.

**Story 5 lands inside it.** Each screen read in docs/screen-reading-inventory.md (sections 1 to 9)
is a Driver method today. The hook and transcript replacements (turns-from-hooks, the Notification
hook) are agentctl events, and a kept screen read stays a Driver method with its reason.
tools/live_e2e.py becomes agentctl's test: its steps already measure these events. A small driver
that calls `Start` with no daemon is the next step for it.

## 3. Layout and order of moves

**Layout:** `go/go.work` lists every module. Each module is a directory `go/<name>/` with its own
`go.mod` (`module github.com/handline/handline/go/<name>`), and daemon glue stays the module at `go/`.
Development and CI build through `go.work`. A release check builds each module with `GOWORK=off`, so a
module that only builds thanks to the workspace is caught. Version tags carry the module's directory, as
Go requires for a module in a subdirectory: `go/agentctl/v0.1.0`. Until another repo imports a module,
tags are optional, and the workspace is the whole contract.

**CI** runs `go test ./...` in each changed module and in every module that requires it, read from
`go list -m -json` in each module. tools/test-go.sh loops over the modules, and check.sh keeps its
"go changed" switch.

How others do it (general knowledge, not fetched; ideas only):
- `golang.org/x/tools` (BSD-3): `gopls` is a nested module in the same repo, tagged `gopls/vX.Y.Z`.
- `open-telemetry/opentelemetry-go` (Apache-2.0): dozens of modules, grouped in a `versions.yaml` and
  tagged together by its `multimod` tool.
- `aws/aws-sdk-go-v2` (Apache-2.0): one module per service, each tagged on its own, with CI scoped to
  changed modules.
- `kubernetes/kubernetes` (Apache-2.0): `staging/src/k8s.io/*` modules wired with `replace`, published
  to separate repos. Heavier than we need.
We borrow the nested-module tag convention (x/tools) and changed-module CI (aws), with no tooling to vendor.

**Order of moves, smallest risk first; each is one PR:**
1. Add `go.work` with the one module and make the test loop module-aware. No package moves.
2. **kit** out: 15 packages, no edges to cut.
3. **protocol** out: 7 packages, no edges to cut.
4. Cut agentctl's seven edges *inside* the single module, one PR each (Sink, Store, Policy, Driver
   types). Behaviour is unchanged and the tests stay where they are.
5. **agentctl** out.
6. **data** out, with `lifecycle.Settings`. Then **accounts**.
7. **taskboard**, then **scheduler**. What remains at `go/` is daemon glue.

Each step changes import paths, not behaviour. A step that needs a behaviour change goes first as its
own PR in the single module.

## Open for the founder

- Is kit (shared leaf utilities) acceptable as a seventh module, and data (`db` and team config) as an
  eighth? The founder's six cannot form an order without them.
- Should agentctl be importable by other repos (public API, version tags), or stay in-repo only?
  That decides whether tags are needed in step 5.
