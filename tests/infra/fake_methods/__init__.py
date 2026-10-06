"""What the conformance harness's fake daemon answers for the methods past hello, whoami and the first reads.

One module per family of methods, each registering its handlers with `fake`; every module in this directory is
imported here, so a branch adding a family adds a file and touches no line another branch touches."""
from __future__ import annotations

import importlib
import pkgutil

# method -> handler(role, params) returning ("ok", result) or ("fail", error)
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
