# Development

```bash
tools/test.sh     # every area file in parallel, ~4800 checks, no network, no tokens
tools/test.sh tests/session/test_daemon_session.py    # one area file on its own (tests/<group>/test_<area>.py)
tools/test.sh -k reconcile --write-labels before.txt   # a subset, labels saved
tools/test.sh --diff-labels before.txt after.txt       # labels that changed state
```

`tools/test.sh` runs them under the machine's venv python, unbuffered, with `OCP_AGENT` unset and a
temp `OCP_HOME`; `tools/suite.sh` is the same thing.

The tests live in `tests/<group>/test_<area>.py` over the shared fixtures in
`tests/support.py`; `tests/test_ocp.py` runs every area file (`-j N` sets how
many at once) and adds their counts.

Design notes are in `design/` and `docs/`. Some of them are still in Korean.
