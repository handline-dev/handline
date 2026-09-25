"""The system clipboard, for a terminal that ignores OSC 52 (iTerm2 by default,
macOS Terminal): `copy` through pbcopy, `image_to_file` for a picture on it."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from . import paths

_SAVE_PNG = '''
set d to the clipboard as «class PNGf»
set f to open for access POSIX file (item 1 of argv) with write permission
set eof f to 0
write d to f
close access f
'''


def copy(text: str) -> bool:
    """`text` on the system clipboard; False when pbcopy is not there or fails."""
    try:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def pasted_dir() -> Path:
    return paths.home() / "pasted"


def image_to_file() -> Path | None:
    """The picture on the clipboard, saved as a PNG under `pasted_dir`: its
    absolute path, or None when the clipboard holds no picture."""
    dest = pasted_dir() / time.strftime("%Y%m%d-%H%M%S.png")
    dest.parent.mkdir(parents=True, exist_ok=True)
    script = "on run argv\n" + _SAVE_PNG + "end run"
    try:
        subprocess.run(["osascript", "-e", script, str(dest)],
                       check=True, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        dest.unlink(missing_ok=True)
        return None
    return dest.resolve()
