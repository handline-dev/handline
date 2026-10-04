"""The system clipboard, for a terminal that ignores OSC 52 (iTerm2 by default,
macOS Terminal): `copy` through the platform's clipboard tool."""
from __future__ import annotations

import shutil
import subprocess

_COPY = (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"],
         ["xsel", "--clipboard", "--input"], ["clip.exe"])


def _first(tools) -> list[str] | None:
    return next((t for t in tools if shutil.which(t[0])), None)


def copy(text: str) -> bool:
    """`text` on the system clipboard; False when no tool is there or it fails."""
    cmd = _first(_COPY)
    if cmd is None:
        return False
    try:
        subprocess.run(cmd, input=text.encode("utf-8"), check=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return True
