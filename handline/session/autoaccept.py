"""The auto-accept switch, on by default: `auto_accept_dialogs` in config.yaml, `hl auto-accept`.
With it on, the daemon answers a dialog the founder gave a standing answer (`team/dialog_rules`).
"""
from __future__ import annotations

from . import idle_close

KEY = "auto_accept_dialogs"


def enabled() -> bool:
    return idle_close.config_flag(KEY, True)


def set_enabled(on: bool) -> None:
    idle_close.set_config_flag(KEY, on)
