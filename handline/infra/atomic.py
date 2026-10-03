"""Replace a file in one step, so a reader sees the old text or the new and a
crash leaves no half-written file."""
from __future__ import annotations

import os
import uuid
from pathlib import Path


def write_text(path: Path, text: str) -> None:
    """Write `text` to a sibling of `path` named for this process and call, so two
    writers never share a temp file, then move it into place."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(text)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
