"""What the conformance harness's fake daemon answers for the methods past hello, whoami and the first reads.

One module per family of methods, each registering its handlers with `fake`; every module in this directory is
imported here, so a branch adding a family adds a file and touches no line another branch touches."""
from __future__ import annotations

import importlib
import threading
import pkgutil

# the connection the call came in on, set by the fake's serve loop: id, send, state (the daemon's), after (run once the reply is sent), on_close
class _Current(threading.local):
    ctx = None


CURRENT = _Current()

# method -> handler(role, method, params) returning ("ok", result) or ("fail", error), or None to leave the call to the daemon's own branches
HANDLERS: dict = {}


def err(code, message, **data):
    return {"code": code, "message": message, "data": {"code": message, **data}}


def fake(*methods: str):
    def register(fn):
        for m in methods:
            HANDLERS[m] = fn
        return fn
    return register


for _m in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__name__}.{_m.name}")
