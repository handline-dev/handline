"""The system clipboard, for a terminal that ignores OSC 52 (iTerm2 by default,
macOS Terminal): `copy` through the platform's clipboard tool, `image_to_file`
for a picture on it. A terminal cannot paste a picture, so the app reads it."""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from . import paths

# JXA, not AppleScript: reading the pasteboard through AppKit needs no scripting
# additions, and a screenshot on it is PNG or TIFF depending on where it came from.
_SAVE_PNG = """
ObjC.import('AppKit');
function run(argv) {
  var pb = $.NSPasteboard.generalPasteboard;
  var d = pb.dataForType('public.png');
  if (d.isNil()) {
    var t = pb.dataForType('public.tiff');
    if (t.isNil()) return 'none';
    d = $.NSBitmapImageRep.imageRepWithData(t)
        .representationUsingTypeProperties($.NSBitmapImageRepFileTypePNG, $({}));
  }
  return d.writeToFileAtomically(argv[0], true) ? 'ok' : 'write failed';
}
"""

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


class ClipboardError(Exception):
    """The clipboard could not be read: the message says why."""


def image_to_file() -> Path | None:
    """The picture on the clipboard, saved as a PNG under `pasted_dir`: its
    absolute path, or None when the clipboard holds no picture. A clipboard that
    could not be read raises `ClipboardError`."""
    dest = pasted_dir() / time.strftime("%Y%m%d-%H%M%S.png")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if shutil.which("osascript"):
            out = subprocess.run(["osascript", "-l", "JavaScript", "-e", _SAVE_PNG, str(dest)],
                                 check=True, capture_output=True, text=True, timeout=10).stdout.strip()
            if out != "ok":
                if out != "none":
                    raise ClipboardError(out)
                return None
        elif cmd := _first(_IMAGE):
            png = subprocess.run(cmd, check=True, capture_output=True, timeout=10).stdout
            if not png.startswith(b"\x89PNG"):
                return None
            dest.write_bytes(png)
        else:
            return None
    except subprocess.CalledProcessError as e:
        dest.unlink(missing_ok=True)
        stderr = e.stderr if isinstance(e.stderr, str) else (e.stderr or b"").decode(errors="replace")
        raise ClipboardError(stderr.strip() or str(e)) from e
    except (OSError, subprocess.SubprocessError) as e:
        dest.unlink(missing_ok=True)
        raise ClipboardError(str(e)) from e
    return dest.resolve()
