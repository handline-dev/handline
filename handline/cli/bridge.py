"""`hl`: the text snapshot of the bridge."""
from __future__ import annotations

import sys

from ..daemon import devmode
from ..infra.yamlcompat import ConfigError
from ..view import tui


def cmd_bridge(args) -> int:
    if args.dev_mode:
        try:
            devmode.set_dev_mode(True)
        except ConfigError as e:
            print(e, file=sys.stderr)
            return 1
    print(tui.snapshot())
    return 0
