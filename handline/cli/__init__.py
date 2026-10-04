"""ocp - command dispatch, one module per command group.

Logic lives here, not in a library nobody reads: SKILL.md stays a stub and
detail is pulled on demand through `ocp guide <topic>` (AGENTS.md rule 3). The
exception is what a human can also do from `ocp bridge` - `handline/actions/` holds
those, so the screen and the command line cannot come to differ about what
unstalling or closing an agent means.

`parser.py` builds the argument parser and holds `main`; each command group's
handlers are in the module named for it.
"""
from .parser import build_parser, main

__all__ = ["build_parser", "main"]
