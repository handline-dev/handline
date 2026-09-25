"""One bridge action, run in a process of its own: `python -m ocp.ui.actrun`.

The request is one JSON object on stdin - {"verb": ..., "args": [...],
"kwargs": {...}} - and the answer is one JSON line on stdout, {"ok": bool,
"said": str}. It runs the verb through `actions.perform` and nothing else, so
the front end calls into the same actions it always did; it only stops waiting
for them itself.

Its stdin is a pipe that has been closed, never the terminal: a program the
action starts cannot read the keys the bridge is reading. If the bridge has gone
by the time it answers, there is nobody to tell, and the action is finished
either way.
"""
from __future__ import annotations

import json
import signal
import sys

from .. import actions


def main() -> int:
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    try:
        req = json.loads(sys.stdin.read())
        ok, said = actions.perform(req["verb"], *req.get("args", []), **req.get("kwargs", {}))
        out = {"ok": ok, "said": said}
    except Exception as e:  # noqa: BLE001 - whatever went wrong is the answer
        out = {"ok": False, "said": f"{type(e).__name__}: {e}"}
    try:
        print(json.dumps(out), flush=True)
    except BrokenPipeError:
        pass
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
