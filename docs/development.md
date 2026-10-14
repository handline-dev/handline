# Development

```bash
tools/test.sh     # every area file in parallel, no network, no tokens
tools/test.sh tests/session/test_daemon_session.py    # one area file on its own (tests/<group>/test_<area>.py)
tools/test.sh -k reconcile --write-labels before.txt   # a subset, labels saved
tools/test.sh --diff-labels before.txt after.txt       # labels that changed state
tools/check.sh main   # what a change needs: Go only for go/ and the apps, the whole suite otherwise
HANDLINE_GO_RACE=1 tools/check.sh main   # the same with go test -race: the auditor's run on a sha, nobody else's
```

`tools/test.sh` runs them under the machine's venv python, unbuffered, with `OCP_AGENT` unset and a
temp `OCP_HOME`; `tools/suite.sh` is the same thing.

The tests live in `tests/<group>/test_<area>.py` over the shared fixtures in
`tests/support.py`; `tests/test_ocp.py` runs every area file (`-j N` sets how
many at once) and adds their counts.

Design notes are in `design/` and `docs/`.

Working on the desktop app itself — the page with hot reload, a dev Electron
instance next to the everyday one, pairing — is in
[app-dev-loop](app-dev-loop.md); running it for real (phones, Tailscale) in
[run-on-real-daemon](run-on-real-daemon.md).
