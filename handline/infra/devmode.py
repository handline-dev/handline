"""The dev mode setting: whether the daemon restarts itself when its code changes."""
from __future__ import annotations

from ..usage import load


# The daemon restarting itself, in place, once it notices its own code changed,
# so a merge needs nobody to kill and relaunch it by hand. Off by default;
# `hl dev-mode on` turns it on (config.yaml, next to `active_cap`).
DEV_MODE_KEY = "dev_mode"


def dev_mode_enabled() -> bool:
    return bool(load._config().get(DEV_MODE_KEY))


def set_dev_mode(on: bool) -> None:
    cfg = load.config_for_write()
    cfg[DEV_MODE_KEY] = bool(on)
    load.write_config(cfg)
