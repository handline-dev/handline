"""The bridge's colours follow the OS: the Textual theme is the one macOS is in, and switches
when the setting changes while the bridge is open. The stylesheets use theme variables only, so
every colour follows the theme."""
from __future__ import annotations

import subprocess

from textual.app import App

POLL_S = 3.0


def os_theme() -> str:
    """The Textual theme that matches the OS's light or dark setting (macOS);
    dark where it cannot be read."""
    try:
        out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                             capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return "textual-dark"
    return "textual-dark" if out.strip() == "Dark" else "textual-light"


def follow_os_theme(app: App) -> None:
    """Set the theme now and again whenever the OS setting changes."""
    app.theme = os_theme()

    def check() -> None:
        want = os_theme()
        if app.theme != want:
            app.theme = want
    app.set_interval(POLL_S, check)
