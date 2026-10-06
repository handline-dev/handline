# contract/

`protocol.json` is the daemon's protocol; `protocol.md` and `protocol.ts` are generated from it (`python3 contract/generate.py`).

## Conformance fixtures

`fixtures/*.json` are scripted exchanges that any daemon (Python, Go) or fake daemon must satisfy. They are the behaviour; prose follows them.

    python3 contract/conformance.py SOCKET [--as ROLE=SOCKET ...] [--var NAME=VALUE ...] [--only TEXT] [FILE ...]

Prints PASS, FAIL or SKIP per fixture. Exit 0: all passed. 1: a fixture failed. 2: usage. 3: nothing failed but something was skipped (`--allow-skip` makes that 0). Every reply must be JSON-RPC 2.0 with exactly one of `result` and `error`, every notice `jsonrpc` 2.0 with a `method`; otherwise the fixture fails. Unix sockets only for now.

A file is `{"fixtures": [Fixture, ...]}`. A fixture is `{name, spec, about, as, steps}`:

- `name`: `area.case`, unique. `spec`: the method (or `x-` section) of `protocol.json` it pins. `about`: one line.
- `as`: the role of the connection: `founder` (the default and SOCKET itself), `agent`, `member`, `anonymous`. A role with no `--as` socket is SKIPPED, never passed.
- Each fixture gets a fresh connection. `steps` run in order; a step has one of `send` (a JSON-RPC request object) or `raw` (a line sent as written), then:
  - `result` or `error`: what the reply with that id must hold. `error` has `code`, `message`, `data`.
  - `notices`: notifications that must arrive after the reply, in this order (others may be interleaved); waits 1 s.
  - `closed`: true or false, whether the daemon closes the connection.
- Matching: an object matches if every key in it matches (extra keys in the reply are fine), an array must match element by element, anything else must be equal. Matchers: `{"$type": string|integer|number|boolean|object|array|null|timestamp}`, `{"$any": true}`, `{"$oneOf": [m, ...]}`, `{"$contains": m}` (some element of an array matches), `{"$regex": r}`, `{"$absent": true}` (the key is missing), `"$exact": true` beside plain keys (the object has no other keys, for closed shapes such as an error's `data`), `{"$save": name}` (keep the value; alone or beside `$type`).
- `{{name}}` in a request string is replaced by a `--var` or a saved value.
- Every method that gets fixtures has at least one refusal or error case.
