"""File paths in text: which stretches of a message are one, and opening one.

A screen links what `find_paths` returns and runs `open_path` on a click; it judges nothing.
Two tiers. A shape that is surely a path - `~/...`, an absolute path with an extension (or that
exists), a quoted one with a slash - is always found. Anything else that might be one (`docs/x.md`,
`name.py`) is found only if it exists, resolved against `bases` (the sender's worktree, the repo
root); a miss is plain text.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from typing import NamedTuple

# An unquoted path stops at whitespace, a quote or a bracket, so it cannot hold a space; a quoted one
# can. It starts where no word, path or URL character sits before it (so `a/b`, `2026/10/01` and the
# `//host/x` of a URL are not paths), and `,` `.` `;` `:` `!` `?` at its end are the sentence's.
_STOP = r"\s\"'`<>()\[\]{}"
_START = r"(?<![\w/:.~@%#=&?+-])"
_EXT = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,7}$")
_LINE = re.compile(r":(\d+)(?::\d+)?$")
_BARE = re.compile(rf"{_START}(?:~/|/)[^{_STOP}]*")
_QUOTED = re.compile(r"(?<!\w)([\"'`])((?:(?!\1)[^\n])+?)\1")
_REL = re.compile(rf"{_START}(?![/~])[\w.@][^{_STOP}]*")
_FOUND: dict[tuple[str, str], tuple[float, str | None]] = {}     # (base, token) -> (when, where it exists)
_FOUND_S = 30
_BASES: dict[str, tuple[float, list[str]]] = {}


class Hit(NamedTuple):
    start: int       # where in the text it begins and ends (the `:line` included)
    end: int
    path: str        # the path itself, `~` not yet expanded
    line: int | None


def _plausible(path: str) -> bool:
    """`~/...` always; an absolute path when its last segment has an extension (`/x/a.md`, `/~~/.md`) or
    it names something that exists. `/loop` or `and /or` is not a path."""
    if path.startswith("~/"):
        return len(path) > 2
    return bool(_EXT.search(path)) or ("/" in path[1:] and os.path.exists(path))


def _resolve(token: str, bases) -> str | None:
    """Where relative `token` exists under one of `bases`, or None. Asked of the disk at most once
    per `_FOUND_S` seconds for each base and token, so a repaint does not stat again."""
    now = time.monotonic()
    for base in bases:
        key = (base, token)
        when, where = _FOUND.get(key, (0.0, None))
        if now - when > _FOUND_S:
            full = os.path.normpath(os.path.join(base, token))
            where = full if os.path.exists(full) else None
            if len(_FOUND) > 2000:
                _FOUND.clear()
            _FOUND[key] = (now, where)
        if where:
            return where
    return None


def find_paths(text: str, bases=()) -> list[Hit]:
    """Every file path in `text`, in order, none overlapping. A path in quotes or backticks may hold
    spaces; a `:42` or `:42:7` after a path is its line. A relative one is a hit only where it
    exists under `bases`, and then its `path` is the full path."""
    hits: list[Hit] = []

    def free(at: int) -> bool:
        return not any(h.start <= at < h.end for h in hits)

    for m in _QUOTED.finditer(text):
        inner = m[2]
        if inner.startswith(("~/", "/")):
            if _plausible(inner):
                hits.append(Hit(m.start(2), m.end(2), inner, None))
        elif "/" in inner and not re.search(r"\s|://", inner):
            hits.append(Hit(m.start(2), m.end(2), _resolve(inner, bases) or inner, None))
    for m in _BARE.finditer(text):
        whole = m[0].rstrip(".,;:!?")
        at = _LINE.search(whole)
        path = whole[:at.start()] if at else whole
        if _plausible(path) and free(m.start()):
            hits.append(Hit(m.start(), m.start() + len(whole), path, int(at[1]) if at else None))
    for m in _REL.finditer(text):
        whole = m[0].rstrip(".,;:!?")
        at = _LINE.search(whole)
        token = whole[:at.start()] if at else whole
        if "://" in token or not ("/" in token or _EXT.search(token)) or len(token) > 200 or not free(m.start()):
            continue
        where = _resolve(token, bases)
        if where:
            hits.append(Hit(m.start(), m.start() + len(whole), where, int(at[1]) if at else None))
    return sorted(hits)


def path_bases(agent: str) -> list[str]:
    """The directories a relative path in `agent`'s message is read against: its worktree, then the repo root."""
    from .. import db
    from ..infra import paths as infra_paths
    from .details import agent_worktree
    now = time.monotonic()
    when, bases = _BASES.get(agent, (0.0, []))
    if now - when > _FOUND_S:
        con = db.connect()
        try:
            tree = agent_worktree(con, agent)
        finally:
            con.close()
        bases = [b for b in (tree, str(infra_paths.repo_root())) if os.path.isdir(b)]
        _BASES[agent] = (now, bases)
    return bases


def open_path(path: str) -> tuple[bool, str]:
    """Open `path` in the system's default app (a folder opens in the file manager). Returns (ok, the
    line to show); a path that is not there is refused, not handed to the opener."""
    full = os.path.expanduser(path)
    if not os.path.exists(full):
        return False, f"not found: {path}"
    tool = "open" if sys.platform == "darwin" else "xdg-open"
    if shutil.which(tool) is None:
        return False, f"no {tool} on this machine to open {path}"
    try:
        subprocess.Popen([tool, full], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        return False, f"could not open {path}: {e}"
    return True, f"opened {path}"
