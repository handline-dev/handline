# contract/

`protocol.json` is the daemon's protocol; `protocol.md` and `protocol.ts` are generated from it (`python3 contract/generate.py`).

## Conformance fixtures

`fixtures/*.json` are scripted exchanges that any daemon (Python, Go) or fake daemon must satisfy. They are the behaviour; prose follows them.

    python3 contract/conformance.py SOCKET [--as ROLE=SOCKET ...] [--var NAME=VALUE ...] [--only TEXT] [--allow-skip] [--allow-missing METHOD ...] [--allow-missing-file PATH] [FILE ...]

Prints PASS, FAIL or SKIP per fixture. Exit 0: all passed. 1: a fixture failed, or was not run for want of a method or a `--var` that no `--allow-missing` names. 2: usage. 3: nothing else is wrong but a role had no socket (`--allow-skip` makes that 0; it covers nothing else). The summary counts passed, failed, skipped by role, not implemented, and needing a var, apart. Every reply must be JSON-RPC 2.0 with exactly one of `result` and `error`, every notice `jsonrpc` 2.0 with a `method`; otherwise the fixture fails. Unix sockets only for now.

A file is `{"fixtures": [Fixture, ...]}`. A fixture is `{name, spec, about, as, steps}`:

- `name`: `area.case`, unique. `spec`: the method (or `x-` section) of `protocol.json` it pins. `about`: one line.
- `as`: the role of the connection: `founder` (the default and SOCKET itself), `agent`, `member`, `anonymous`. A role with no `--as` socket is SKIPPED, never passed; `--allow-skip` covers only this.
- A fixture is reported NEEDVAR when it needs a `--var` that was not given, and MISSING ("method not implemented by this daemon") when its `spec` is a method the daemon does not list in `hello`. Both fail the run unless the fixture's `spec` method is named by `--allow-missing METHOD` (repeatable) or in `--allow-missing-file PATH` (one method per line, `#` comments): a daemon that is still growing keeps that list, and shrinks it as methods land. A method the daemon lists and then fails to serve is a FAIL.
- Each fixture gets a fresh connection. `steps` run in order; a step has one of `send` (a JSON-RPC request object) or `raw` (a line sent as written), then:
  - `result` or `error`: what the reply with that id must hold. `error` has `code`, `message`, `data`.
  - `notices`: notifications that must arrive after the reply, in this order (others may be interleaved); waits 1 s.
  - `closed`: true or false, whether the daemon closes the connection.
- Matching: an object matches if every key in it matches (extra keys in the reply are fine), an array must match element by element, anything else must be equal. Matchers: `{"$type": string|integer|number|boolean|object|array|null|timestamp}`, `{"$any": true}`, `{"$oneOf": [m, ...]}`, `{"$contains": m}` (some element of an array matches), `{"$regex": r}`, `{"$absent": true}` (the key is missing), `"$exact": true` beside plain keys (the object has no other keys, for closed shapes such as an error's `data`), `{"$save": name}` (keep the value; alone or beside `$type`).
- `{{name}}` in a request string is replaced by a `--var` or a saved value.
- Every method that gets fixtures has at least one refusal or error case.

## Running them

- Founder: the connection proves it by the founder key, which a fixture sends as `{{founder_key}}` in `hello`. Pass it with `--var founder_key=KEY`. The Python daemon keeps it in its protected store (`sessiontoken.founder_key_held()`, minted by `sessiontoken.ensure_founder_key()`); the Go daemon's own test harness mints one.
- Anonymous: the same socket, a hello without the key.
- Agent and member cannot be faked: an agent is a peer process that descends from a recorded session, a member a paired device. A CI run supplies them with `--as agent=SOCKET` from a process started inside such a session, and `--as member=SOCKET` over a device token; without them those fixtures SKIP and the run exits 3, which `--allow-skip` turns into 0.
- A socket can also be a function returning a connected socket (`conformance.run_all`), which is how the tests serve the Python daemon over a socket pair.
