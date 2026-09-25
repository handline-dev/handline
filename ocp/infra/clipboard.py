"""The system clipboard, for a terminal that ignores OSC 52 (iTerm2 by default,
macOS Terminal): `copy` through the platform's clipboard tool, `image_to_file`
for a picture on it. A terminal cannot paste a picture, so the app reads it."""
from __future__ import annotations

import shutil
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


_COPY = (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"],
         ["xsel", "--clipboard", "--input"], ["clip.exe"])
_IMAGE = (["wl-paste", "--type", "image/png"],
          ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"])


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


def pasted_dir() -> Path:
    return paths.home() / "pasted"


def image_to_file() -> Path | None:
    """The picture on the clipboard, saved as a PNG under `pasted_dir`: its
    absolute path, or None when the clipboard holds no picture."""
    dest = pasted_dir() / time.strftime("%Y%m%d-%H%M%S.png")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if shutil.which("osascript"):
            script = "on run argv\n" + _SAVE_PNG + "end run"
            subprocess.run(["osascript", "-e", script, str(dest)],
                           check=True, capture_output=True, timeout=10)
        elif cmd := _first(_IMAGE):
            png = subprocess.run(cmd, check=True, capture_output=True, timeout=10).stdout
            if not png.startswith(b"\x89PNG"):
                return None
            dest.write_bytes(png)
        else:
            return None
    except (OSError, subprocess.SubprocessError):
        dest.unlink(missing_ok=True)
        return None
    return dest.resolve()
