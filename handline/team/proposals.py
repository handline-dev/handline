"""Reading the roster proposals in `hr/<id>.yaml` (`team/hr` writes and decides them). Apart from `team/hr` so a
reader, such as the Stop hook's gate, does not load what deciding them needs."""
from __future__ import annotations

from pathlib import Path

from ..infra import paths
from ..infra.yamlcompat import ConfigError, load

ITEM_KINDS = ("add", "retire", "rename", "link", "unlink", "team", "settings", "context", "schedule", "global")
BUNDLE = "bundle"
KINDS = ITEM_KINDS + (BUNDLE,)


def dir_() -> Path:
    d = paths.home() / "hr"
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_proposal(data) -> bool:
    """Whether a parsed file is a proposal at all: what every reader of the
    listing takes for granted, so a map that is something else is not one."""
    return (isinstance(data, dict) and "id" in data and data.get("kind") in KINDS
            and bool(data.get("agent")))


def listing(unreadable: list[str] | None = None) -> list[dict]:
    """Every proposal that can be read. A file that cannot be - it does not
    parse, it is not text, or it parses to something that is not a proposal -
    is left out and, when `unreadable` is given, named there with why: the
    bridge's waiting list and `hl hr list` both read this, and one bad file
    must not take either down."""
    out = []
    for f in sorted(dir_().glob("*.yaml")):
        try:
            data = load(f.read_text())
        except (ConfigError, UnicodeDecodeError, OSError) as e:
            if unreadable is not None:
                unreadable.append(f"{f.name}: {e}")
            continue
        if is_proposal(data):
            out.append(data)
        elif unreadable is not None:
            unreadable.append(f"{f.name}: not a proposal")
    return out
