"""The protected store: what only the founder may say, kept where no agent can write it.

Everything under ~/.orcapipe - agent.yaml, the db, every file ocp keeps - is
writable by every agent's sandbox, so nothing kept there can prove that the
founder, and not an agent, asked for it. This store lives outside it, under
~/.config/orcapipe/protected, which the sandbox does not list as writable: a
write from inside an agent's session fails with the operating system's own
refusal, and only the founder's shell, the screen and the daemon (all outside
the sandbox) can make one. Reading is open. It holds decisions, never secrets:
a credential belongs nowhere in ocp (AGENTS.md rule 1).

One file per entry, `<namespace>/<name>`, under a folder for this OCP_HOME so
two installations on one machine keep their own. `put` and `delete` also refuse
when $OCP_AGENT names an agent, so where the sandbox is off the answer is still no from
ocp itself - a convenience, not the lock; the lock is the location.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from . import paths

_PLAIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class NotTheFounder(PermissionError):
    pass


def root_for(home: Path) -> Path:
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(home.resolve()))
    return paths.user_home() / ".config" / "orcapipe" / "protected" / slug


def root() -> Path:
    return root_for(paths.home())


def files(home: Path) -> list[Path]:
    """Every entry kept for `home`, relative to its store, for a backup."""
    base = root_for(home)
    if not base.is_dir():
        return []
    return sorted(p.relative_to(base) for p in base.rglob("*")
                  if p.is_file() and not p.name.startswith(".put-"))


def _entry(namespace: str, name: str) -> Path:
    for part in (namespace, name):
        if not _PLAIN.fullmatch(part):
            raise ValueError(f"not a plain name: {part!r}")
    return root() / namespace / name


def get(namespace: str, name: str) -> str | None:
    try:
        return _entry(namespace, name).read_text()
    except (FileNotFoundError, NotADirectoryError):
        return None


def in_agent_session() -> bool:
    """$OCP_AGENT names an agent - anyone but the founder's own `human`."""
    from ..team import ask
    return os.environ.get("OCP_AGENT", "") not in ("", ask.HUMAN)


def _founder_only() -> None:
    if in_agent_session():
        raise NotTheFounder("this is the founder's to set, from a terminal or the bridge, "
                            "not an agent's")


def put(namespace: str, name: str, value: str) -> None:
    """Record `value`. PermissionError, the OS's or ours, when the caller is an
    agent's session."""
    _founder_only()
    path = _entry(namespace, name)
    if not path.parent.exists():
        path.parent.mkdir(parents=True)
        for d in (root(), path.parent):
            d.chmod(0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".put-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(value)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def delete(namespace: str, name: str) -> None:
    _founder_only()
    _entry(namespace, name).unlink(missing_ok=True)


def exposure() -> list[str]:
    """What makes this store no lock at all, for `ocp doctor`: it sits inside a
    folder the agents write, or something other than its owner can write to it.
    Whether an agent's sandbox really refuses the write is the sandbox's own
    setting, which this cannot see."""
    out = []
    here = root().resolve()
    ocp_home = paths.home().resolve()
    if here == ocp_home or ocp_home in here.parents:
        out.append(f"{here} is inside {ocp_home}, which every agent can write")
    node = here
    while not node.exists() and node != node.parent:
        node = node.parent
    if node.exists() and node.stat().st_mode & 0o022:
        out.append(f"{node} can be written by its group or others")
    return out
