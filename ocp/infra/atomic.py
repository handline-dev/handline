"""Replace a file in one step, so a reader sees the old text or the new and a
crash leaves no half-written file."""
from __future__ import annotations

from pathlib import Path


def write_text(path: Path, text: str) -> None:
    """Write `text` to a sibling of `path`, then move it into place."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)
