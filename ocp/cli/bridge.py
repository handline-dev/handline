"""`ocp bridge`, and the choice between its screen and its text snapshot."""
from __future__ import annotations

import sys

from ..ui import tui


def _import_bridge_textual():
    """The one line that can raise - a name tests replace to force the
    failure `_bridge_textual_module` classifies, without a real broken
    import."""
    from ..ui import bridge_textual
    return bridge_textual


def _missing_bridge_package(e: ModuleNotFoundError) -> bool:
    """Whether `e` is `textual` or `rich` itself not being on this
    interpreter, not some other name a broken import behind them failed on."""
    return bool(e.name) and e.name.split(".")[0] in ("textual", "rich")


def _bridge_textual_module():
    """`bridge_textual`, or None if `textual`/`rich` are missing (auditor
    #2138 should-fix 1): any other `ModuleNotFoundError` is a real bug behind
    the two, and must be seen rather than silently read as "the package is
    missing" and served the old screen."""
    try:
        return _import_bridge_textual()
    except ModuleNotFoundError as e:
        if _missing_bridge_package(e):
            return None
        raise


def cmd_bridge(args) -> int:
    # The bridge is a Textual app. Run once, or into a pipe, it is `tui.snapshot`:
    # plain text that needs no `textual`.
    if args.once or not sys.stdout.isatty():
        print(tui.snapshot())
        return 0
    bridge_textual = _bridge_textual_module()
    if bridge_textual is None:
        print("ocp bridge needs the `textual` package.\n"
              "`ocp venv install` puts it in $OCP_HOME/venv (see requirements.txt).")
        return 1
    return bridge_textual.run()
