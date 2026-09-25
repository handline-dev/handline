"""Which folders `ocp` may answer claude's trust prompt for, on the
founder's own say-so - never inferred from the roster or Orca's own layout.

Adding one goes through the same gate `hr.py` uses for the roster: any agent
may propose, only the founder approves, and applying is the one place the
file changes. Removing only shrinks what ocp may answer for, so it needs no
gate.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from . import ask as askmod, hr
from .. import db
from ..infra import paths
from ..infra.yamlcompat import dump, load

# JSON, not yamlcompat: this file is a list of maps, and the bundled parser -
# built for agent.yaml's flat scalars - only writes and reads a flat map.
# Proposal files below stay flat, so they still use it like hr.py's do.


def _path() -> Path:
    return paths.home() / "trust_roots.json"


def list_roots() -> list[dict]:
    """Every declared root, as {root, why, added_by, added_at}."""
    p = _path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _save(rows: list[dict]) -> None:
    p = _path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=2))
    tmp.replace(p)


def _resolved(path) -> str | None:
    """The canonical form a root is stored and matched under, or None if it
    cannot be resolved. One function so `is_declared` and `remove` can never
    disagree about what a root's own spelling is."""
    try:
        return str(Path(path).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        return None


def is_declared(path) -> bool:
    """Is `path` inside a root the founder has declared, or one of its worktrees?"""
    target = _resolved(path)
    if target is None:
        return False
    target_p = Path(target)
    for row in list_roots():
        root = _resolved(row.get("root"))
        if root is None:
            continue
        root_p = Path(root)
        if target_p == root_p or root_p in target_p.parents:
            return True
    return False


def _proposal_dir() -> Path:
    d = paths.home() / "trust_proposals"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _proposal_path(pid: str) -> Path:
    return _proposal_dir() / f"{pid}.yaml"


def load_proposal(pid: str) -> dict | None:
    p = _proposal_path(pid)
    if not p.exists():
        return None
    data = load(p.read_text())
    return data if isinstance(data, dict) else None


def _save_proposal(prop: dict) -> None:
    p = _proposal_path(prop["id"])
    tmp = p.with_suffix(".tmp")
    tmp.write_text(dump(prop))
    tmp.replace(p)


def listing() -> list[dict]:
    out = []
    for f in sorted(_proposal_dir().glob("*.yaml")):
        data = load(f.read_text())
        if isinstance(data, dict):
            out.append(data)
    return out


def propose(root: str, why: str, requester: str) -> tuple[bool, str]:
    """Ask the founder to add `root` to standing consent. Nothing changes yet."""
    if not (why or "").strip():
        return False, "say why: the founder is deciding, not rubber-stamping"
    resolved = _resolved(root) or str(Path(root).expanduser())
    if is_declared(resolved):
        return False, f"{resolved} is already inside a declared root"
    prop = {"id": uuid.uuid4().hex[:8], "root": resolved, "why": why.strip(),
            "requested_by": requester, "created": int(time.time()), "state": "proposed"}
    ok, said, rid = askmod.ask(askmod.HUMAN, _escalation(prop), requester=requester)
    if not ok:
        return False, f"could not raise it: {said}"
    prop["request"] = rid
    _save_proposal(prop)
    return True, (f"proposal {prop['id']} raised as #{rid}. Nothing changes until the "
                  f"founder answers it; then `ocp trust apply {prop['id']}`.")


def _root_read(body: str | None, requester: str) -> str | None:
    """The root the founder was shown: the path in the escalation's own first
    line, and only if that line names this requester."""
    m = re.fullmatch(r"\[trust\] add (.+) - asked by (\S+)", (body or "").split("\n", 1)[0])
    return m.group(1) if m and m.group(2) == requester else None


def _escalation(prop: dict) -> str:
    return (f"[trust] add {prop['root']} - asked by {prop['requested_by']}\n\n"
            f"{prop['why']}\n\n"
            f"ocp would press \"Yes, I trust this folder\" for this path and "
            f"everything under it, without asking again.\n\n"
            f"Answer `approve` or `reject` (proposal {prop['id']}).")


def enact(root: str, why: str) -> tuple[bool, str]:
    """The founder adding a root directly. No proposal to approve."""
    resolved = _resolved(root) or str(Path(root).expanduser())
    if is_declared(resolved):
        return False, f"{resolved} is already inside a declared root"
    rows = list_roots()
    rows.append({"root": resolved, "why": (why or "").strip() or "added by the founder",
                 "added_by": askmod.HUMAN, "added_at": int(time.time())})
    _save(rows)
    return True, f"{resolved} is now a declared root"


def apply(pid: str, con=None) -> tuple[bool, str]:
    """Carry out a trust proposal the founder approved."""
    prop = load_proposal(pid)
    if not prop:
        return False, f"no proposal {pid}"
    if prop.get("state") == "applied":
        return False, f"proposal {pid} was already applied"
    own = con is None
    con = con or db.connect()
    try:
        row = db.get_request(con, int(prop.get("request") or 0))
        if row is None:
            return False, f"proposal {pid} has no escalation to check - raise it again"
        if row["state"] == "queued":
            return False, (f"the founder has not answered #{row['id']} yet. It is on "
                           f"`ocp bridge`, top panel.")
        verdict = row["verdict"]
        call = hr.decision(verdict)
        if call == "rejected":
            prop["state"] = "rejected"
            prop["verdict"] = verdict
            _save_proposal(prop)
            return False, f"proposal {pid} was rejected: {(verdict or '').strip()[:120]}"
        if call != "approved":
            return False, (f"#{row['id']} was answered "
                           f"{(db.shown_verdict(row) or '').strip()[:60]!r}, "
                           f"which is not a decision.")
        # What gets added is exactly what the founder read: the path on the
        # request row's first line, which this file cannot edit, must be the
        # proposal's root - a substring test let a proposal edited to a
        # parent directory through.
        read = _root_read(row["body"], row["requester"])
        if read is None or _resolved(read) != _resolved(prop["root"]):
            return False, (f"proposal {pid}'s root does not match what #{row['id']} "
                           f"asked the founder to approve - raise it again")
        rows = list_roots()
        rows.append({"root": prop["root"], "why": prop["why"],
                     "added_by": prop["requested_by"], "added_at": prop["created"]})
        _save(rows)
        prop["state"] = "applied"
        prop["applied"] = int(time.time())
        prop["verdict"] = verdict
        _save_proposal(prop)
        return True, f"{prop['root']} is now a declared root"
    finally:
        if own:
            con.close()


def remove(root: str) -> tuple[bool, str]:
    """Take a root off standing consent."""
    resolved = _resolved(root) or str(Path(root).expanduser())
    rows = list_roots()
    kept = [r for r in rows if _resolved(r.get("root")) != resolved]
    if len(kept) == len(rows):
        return False, f"{resolved} is not a declared root"
    _save(kept)
    return True, f"{resolved} is no longer a declared root"
