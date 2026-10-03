"""`hl`, and the choice between its screen and its text snapshot."""
from __future__ import annotations

import sys

from ..daemon import devmode
from ..infra.yamlcompat import ConfigError
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
    if args.pid:
        from ..ui import stall      # needs neither textual nor rich
        pid = stall.running_pid()
        print(pid if pid else "the screen is not running")
        return 0 if pid else 1
    if args.dev_mode:
        try:
            devmode.set_dev_mode(True)
        except ConfigError as e:
            print(e, file=sys.stderr)
            return 1
    # The bridge is a Textual app. Run once, or into a pipe, it is `tui.snapshot`:
    # plain text that needs no `textual`.
    if args.once or not sys.stdout.isatty():
        print(tui.snapshot())
        return 0
    bridge_textual = _bridge_textual_module()
    if bridge_textual is None:
        print("hl needs the `textual` package.\n"
              "`hl venv install` puts it in $OCP_HOME/venv (see requirements.txt).")
        return 1
    return bridge_textual.run()
