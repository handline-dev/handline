"""The bridge's home page. `bridge_textual.Dashboard` is the previous home page,
kept unchanged; `HomePage` starts as the same screen and is the one `BridgeApp`
opens, so edits here never touch the Dashboard."""
from __future__ import annotations

from .bridge_textual import Dashboard


class HomePage(Dashboard):
    pass
