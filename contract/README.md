# contract/

`protocol.json` is the daemon's protocol; `protocol.md` and `protocol.ts` are generated from it (`python3 contract/generate.py`).

## Conformance fixtures

`fixtures/*.json` are scripted exchanges that any daemon (Python, Go) or fake daemon must satisfy. They are the behaviour; prose follows them.

    python3 contract/conformance.py SOCKET [--as ROLE=SOCKET ...] [--var NAME=VALUE ...] [--only TEXT] [--allow-skip] [--allow-missing METHOD ...] [--allow-missing-file PATH] [--allow-needvar-file PATH] [FILE ...]

Prints PASS, FAIL or SKIP per fixture. Exit 0: all passed. 1: a fixture failed, or was not run for want of a method or a `--var` that no `--allow-missing` names. `--allow-needvar-file PATH` names fixtures (one per line) that may lack their `--var`; a name there that passed or does not exist is an error, so the list cannot rot. 2: usage. 3: nothing else is wrong but a role had no socket (`--allow-skip` makes that 0; it covers nothing else). The summary counts passed, failed, skipped by role, not implemented, and needing a var, apart. Every reply must be JSON-RPC 2.0 with exactly one of `result` and `error`, every notice `jsonrpc` 2.0 with a `method`; otherwise the fixture fails. Unix sockets only for now.

A file is `{"fixtures": [Fixture, ...]}`. A fixture is `{name, spec, about, as, steps}`:

- `name`: `area.case`, unique. `spec`: the method (or `x-` section) of `protocol.json` it pins. `about`: one line.
- `as`: the role of the connection: `founder` (the default and SOCKET itself), `agent`, `member`, `anonymous`. A role with no `--as` socket is SKIPPED, never passed; `--allow-skip` covers only this.
- A fixture is reported NEEDVAR when it needs a `--var` that was not given, and MISSING ("method not implemented by this daemon") when its `spec` is a method the daemon does not list in `hello`. Both fail the run unless the fixture's `spec` method is named by `--allow-missing METHOD` (repeatable) or in `--allow-missing-file PATH` (one method per line, `#` comments): a daemon that is still growing keeps that list, and shrinks it as methods land. A method the daemon lists and then fails to serve is a FAIL.
- Each fixture gets a fresh connection. `steps` run in order; a step has one of `send` (a JSON-RPC request object) or `raw` (a line sent as written), then:
  - `result` or `error`: what the reply with that id must hold. `error` has `code`, `message`, `data`.
  - `notices`: notifications that must arrive after the reply, in this order (others may be interleaved); waits 1 s, or `notices_within_s` (a bound on the wait, never a pause: it returns as soon as they have come).
  - `notice_next`: the very next notification must match, none before it; it is how a fixture shows no gap and no duplicate. It is checked before `notices`.
  - `closed`: true or false, whether the daemon closes the connection.
  - A step with `send` and no `result`, `error` or `closed` only sends: the call is pipelined, and a later step `{"await": id, "result"|"error": ...}` reads its reply, whatever order the replies came in. `within_s` on a step bounds how long its reply may take (default 5), which is how a fixture shows a waiting call did not hold up the one behind it. `at_least_s` is the reverse: the reply must take at least that long from its send, which shows a wait really waited.
- Matching: an object matches if every key in it matches (extra keys in the reply are fine), an array must match element by element, anything else must be equal. Matchers: `{"$type": string|integer|number|boolean|object|array|null|timestamp}`, `{"$any": true}`, `{"$oneOf": [m, ...]}`, `{"$contains": m}` (some element of an array matches), `{"$each": m}` (every element matches; an empty array does), `{"$regex": r}`, `{"$absent": true}` (the key is missing), `"$exact": true` beside plain keys (the object has no other keys, for closed shapes such as an error's `data`), `{"$save": name}` (keep the value; alone or beside `$type`), `{"$above": name}` and `{"$next": name}` (an integer above, or exactly one above, a saved one), `{"$b64regex": r}` (a base64 string whose decoded text matches).
- `{{name}}` in a request string is replaced by a `--var` or a saved value (a name a step of the same fixture saves needs no `--var`).
- More than one connection: `with` is `{name: role}` of extra connections, opened with the first, and a step's `on: name` runs it there (default: the fixture's own). A step with only `closed` (and `on`) waits for that connection to be closed. `sleep_s` on a step waits before it.
- `for_each: "method"` repeats the steps marked `each` once for every method of `protocol.json` (less `except`) and once for a name it does not have (`@method` in the step stands for the name), so a method added to the spec is covered without a fixture edit.
- Every method that gets fixtures has at least one refusal or error case.

## Running them

- Founder: the connection proves it by the founder key, which a fixture sends as `{{founder_key}}` in `hello`. Pass it with `--var founder_key=KEY`. The Python daemon keeps it in its protected store (`sessiontoken.founder_key_held()`, minted by `sessiontoken.ensure_founder_key()`); the Go daemon's own test harness mints one.
- Anonymous: the same socket, a hello without the key.
- A role limit can sit inside one method: an agent may call `session.stop` with mode exit or close, never kill (refused, gate `role`). `session_stop.json` pins the kill refusal.
- `shell_input.json` needs `--var shell_agent=NAME`: an agent whose pane is a shell prompt (its session has exited). `session.input` is refused there, gate `state`; the harness cannot make that screen, so the daemon under test must already be in that state.
- `terminal_bytes.json` needs `--var bytes_agent=NAME`: an agent whose terminal is a tmux window showing a shell prompt (its session has exited), since the fixtures type into it; `--var text_only_agent=NAME` is one whose terminal the daemon cannot serve as bytes (Orca-hosted). The fake daemon (`tests/infra/fake_methods/tmux.py`) plays that terminal with a scripted shell, so every fixture of the file passes against it; a real daemon runs them on real tmux (the Python daemon's tests, if tmux is there, and say so if not). Fixtures that do not need the var (the format choice, `terminal.input`'s checks) run on any daemon. The slow-client fixture floods the pane with 16 MiB and reads nothing from the attach until the end; the rule it shows is counted in bytes, so no fixture depends on how fast anything runs.
- Agent and member cannot be faked: an agent is a peer process that descends from a recorded session, a member a paired device. A CI run supplies them with `--as agent=SOCKET` from a process started inside such a session, and `--as member=SOCKET` over a device token; without them those fixtures SKIP and the run exits 3, which `--allow-skip` turns into 0.
- A socket can also be a function returning a connected socket (`conformance.run_all`), which is how the tests serve the Python daemon over a socket pair.

## Other checks

The web client's tests are not part of `tools/test.sh`: run `npm test` in `web/` (Node's test runner over `web/test/*.test.ts`).

## Prompt captures

`prompts/*.json` are screens of open prompts and what `prompt.list` must read from each: `{name, source, screen, expect}`. `screen` is the terminal's rows as captured, `expect` the prompt's `kind`, `tool`, `command`, `reason` and option `keys`. A field the screen does not state is null. `tests/daemon/test_prompt_fields.py` runs each through the Python daemon's screen watch; the Go daemon is to read the same screens to the same answers. Not covered: the folder-trust screen and the bypass warning (the screen watch does not list them: trust has its own path, and the bypass screen's options are unnumbered) and an edit approval (never captured); one that reaches the watch as a dialog is listed with its raw lines and no fields.

## The network transport

A daemon that has a network listener (Go) speaks the same JSON-RPC over a WebSocket on TLS, and the client pins the certificate fingerprint it read from the pairing uri. The role `unpaired` is a connection on that transport with no token, and `member` one that said hello with a device token. `devices.json` runs on them:

    python3 contract/conformance.py FOUNDER_SOCKET --as unpaired=SOCKET --var founder_key=KEY --var terminal_agent=NAME contract/fixtures/devices.json

`SOCKET` is a Unix socket path, or a connector function (`conformance.run_all`), that is already past TLS and the fingerprint check: this harness opens no WebSocket and checks no certificate. A listener under test therefore gives the harness a socket that behaves as one network connection (the Go daemon's test exposes one), and the founder socket is its local one, which pairs and revokes. A client tells "unpaired" from "too old" by the refusal itself: gate `unpaired` means not paired, and the same words come back for a revoked or never issued token. A client that is too old is knowable only after pairing, when the first authenticated hello carries the version. What this shows: no data to an unpaired caller for any method of the spec, hello and an unknown name included; a code that redeems once; a revoke that closes the connection and attach; a member refused above member. What it does not: that the listener refuses a certificate other than the pinned one, that it refuses plaintext, or that it binds only the configured address. The Go listener's own tests pin those, and the Python fake (`tests/infra/test_conformance.py`, `FakeNetwork`) shows only the rules above.
