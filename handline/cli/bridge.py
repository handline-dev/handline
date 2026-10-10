"""`hl`: the team's status, printed as text."""
from __future__ import annotations

import sys

from ..daemon import devmode
from ..infra.yamlcompat import ConfigError
from .health import cmd_status


def cmd_bridge(args) -> int:
    if args.dev_mode:
        try:
            devmode.set_dev_mode(True)
        except ConfigError as e:
            print(e, file=sys.stderr)
            return 1
    args.json = False
    cmd_status(args)                # its exit code says whether an account may take work; the screen only shows it
    return 0
