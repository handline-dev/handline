"""Which folders `hl` may answer claude's trust prompt for, on the
founder's own say-so - never inferred from the roster or Orca's own layout.

Adding one goes through the same gate `hr.py` uses for the roster: any agent
may propose, only the founder approves, and applying is the one place the
file changes. Removing only shrinks what ocp may answer for, so it needs no
gate.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path

from . import ask as askmod, decisions
from .. import db
from ..infra import atomic, paths, protected, textsafe
from ..infra.yamlcompat import dump, load

# JSON, not yamlcompat: this file is a list of maps, and the bundled parser -
# built for agent.yaml's flat scalars - only writes and reads a flat map.
# Proposal files below stay flat, so they still use it like hr.py's do.


# The declared roots are the founder's alone to change, so they live in the
# protected store, not in a file every agent can write.
_NS = "trust"
_ROOTS = "roots.json"


def list_roots() -> list[dict]:
    """Every declared root, as {root, why, added_by, added_at}."""
    raw = protected.get(_NS, _ROOTS)
    if raw is None:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _save(rows: list[dict]) -> None:
    protected.put(_NS, _ROOTS, json.dumps(rows, indent=2))


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


_MCP = "mcp_servers.json"


def _entry_hash(entry) -> str:
    return hashlib.sha256(json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def mcp_servers(folder) -> dict[str, str]:
    """The servers `folder`'s .mcp.json names, as {name: hash of its whole entry (the command, args and
    environment included)}; empty when there is no readable file."""
    try:
        servers = json.loads((Path(folder).expanduser() / ".mcp.json").read_text()).get("mcpServers")
    except (OSError, ValueError, AttributeError):
        return {}
    return {k: _entry_hash(v) for k, v in servers.items() if isinstance(k, str)} if isinstance(servers, dict) else {}


def _approved_mcp() -> set[str]:
    raw = protected.get(_NS, _MCP)
    try:
        data = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return set()
    return {x for x in data if isinstance(x, str)} if isinstance(data, list) else set()


def mcp_approved(folder) -> list[str]:
    """The servers in `folder`'s .mcp.json a launch may enable without the vendor asking: the folder is
    under a declared root and the founder approved that server's entry as it is now (`approve_mcp`). An
    agent can write the file in its own worktree, so a name alone is never enough: a server added, or one
    whose command changed, is not approved and is asked about as before."""
    if not is_declared(folder):
        return []
    done = _approved_mcp()
    return sorted(n for n, h in mcp_servers(folder).items() if f"{n}:{h}" in done)


def mcp_unapproved(folder) -> list[str]:
    """The servers in `folder`'s .mcp.json `mcp_approved` leaves to the vendor's own question."""
    return sorted(set(mcp_servers(folder)) - set(mcp_approved(folder))) if is_declared(folder) \
        else sorted(mcp_servers(folder))


def approve_mcp(folder) -> tuple[bool, str]:
    """The founder approving every server in `folder`'s .mcp.json as it reads now - by content, so the
    same name with another command is a new question. The folder must be under a declared root, or a
    launch would never use it."""
    if not is_declared(folder):
        return False, f"{folder} is not inside a declared root; `hl trust add` it first"
    servers = mcp_servers(folder)
    if not servers:
        return False, f"{folder} has no readable .mcp.json with servers"
    try:
        protected.put(_NS, _MCP, json.dumps(sorted(_approved_mcp() | {f"{n}:{h}" for n, h in servers.items()})))
    except PermissionError as e:
        return False, str(e)
    return True, "approved: " + ", ".join(sorted(servers))


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
    atomic.write_text(p, dump(prop))


def _waits_on(prop: dict, rid: int, tid: int | None) -> bool:
    """Is request `rid` (task `tid`'s card) the decision this proposal waits on?"""
    if prop.get("task"):
        return tid is not None and int(prop["task"]) == tid
    return int(prop.get("request") or 0) == rid


def open_proposal_of(rid: int, con=None) -> str | None:
    """The id of the trust proposal still waiting on an answer whose decision is request `rid`
    (the card of its decision task)."""
    props = [p for p in listing() if p.get("state") == "proposed"]
    if not props:
        return None
    own = con is None
    con = con or db.connect()
    try:
        tid = decisions.task_of_card(con, rid)
    finally:
        if own:
            con.close()
    for p in props:
        if _waits_on(p, rid, tid):
            return p["id"]
    return None


def listing() -> list[dict]:
    out = []
    for f in sorted(_proposal_dir().glob("*.yaml")):
        data = load(f.read_text())
        if isinstance(data, dict):
            out.append(data)
    return out


def propose(root: str, why: str, requester: str) -> tuple[bool, str]:
    """Ask the founder to add `root` to standing consent. Nothing changes yet."""
    ok, said, _ = propose_with_id(root, why, requester)
    return ok, said


def propose_with_id(root: str, why: str, requester: str) -> tuple[bool, str, str | None]:
    """`propose`, and the proposal's id when one was raised."""
    if not (why or "").strip():
        return False, "say why: the founder is deciding, not rubber-stamping", None
    resolved = _resolved(root) or str(Path(root).expanduser())
    if is_declared(resolved):
        return False, f"{resolved} is already inside a declared root", None
    prop = {"id": uuid.uuid4().hex[:8], "root": resolved, "why": why.strip(),
            "requested_by": requester, "created": int(time.time()), "state": "proposed"}
    from .. import actions      # a decision is a task of the founder's
    ok, said, rid = actions.open_decision(requester, _decision_text(prop))
    if not ok:
        return False, f"could not raise it: {said}", None
    prop["request"] = rid
    con = db.connect()
    try:
        prop["task"] = decisions.task_of_card(con, rid)       # keyed by its decision task
    finally:
        con.close()
    _save_proposal(prop)
    return True, (f"proposal {prop['id']} raised as #{rid}. Nothing changes until the "
                  f"founder answers it; an approve is applied then, and you are told how it went."), prop["id"]


def _root_read(body: str | None, requester: str) -> str | None:
    """The root the founder was shown: the path in the decision's own first
    line, and only if that line names this requester."""
    m = re.fullmatch(r"\[trust\] add (.+) - asked by (\S+)", (body or "").split("\n", 1)[0])
    return m.group(1) if m and m.group(2) == requester else None


def _decision_text(prop: dict) -> str:
    return (f"[trust] add {prop['root']} - asked by {prop['requested_by']}\n\n"
            f"{prop['why']}\n\n"
            f"ocp would press \"Yes, I trust this folder\" for this path and "
            f"everything under it, without asking again.\n\n"
            f"Answer `approve` or `reject` (proposal {prop['id']}).")


def subject_of(prop: dict) -> str:
    """What the founder is deciding on: the root as it will be matched."""
    return _resolved(prop.get("root")) or str(prop.get("root"))


def answered(prop: dict, row) -> tuple[str | None, str | None]:
    """(subject, refusal) for the answer given on `row`: the root the founder
    was shown, read off the decision itself - not the draft, which an agent
    can edit - so apply can tell whether the draft still names it."""
    read = _root_read(row["body"], row["requester"])
    if read is None:
        return None, f"#{row['id']} does not name the root it asked the founder to approve"
    return _resolved(read) or read, None


def enact(root: str, why: str) -> tuple[bool, str]:
    """The founder adding a root directly. No proposal to approve."""
    resolved = _resolved(root) or str(Path(root).expanduser())
    if is_declared(resolved):
        return False, f"{resolved} is already inside a declared root"
    rows = list_roots()
    rows.append({"root": resolved, "why": (why or "").strip() or "added by the founder",
                 "added_by": askmod.HUMAN, "added_at": int(time.time())})
    try:
        _save(rows)
    except PermissionError as e:
        return False, str(e)
    return True, f"{resolved} is now a declared root"


def settle_moot() -> list[str]:
    """T797: a proposal for a root the founder has already declared is moot - nothing is left to
    apply. Each open one is settled as withdrawn (never as approved) and its id returned."""
    gone = []
    for prop in listing():
        if prop.get("state") == "proposed" and prop.get("root") and is_declared(prop["root"]):
            prop["state"] = "withdrawn"
            prop["verdict"] = "moot: that root is already declared"
            _save_proposal(prop)
            gone.append(prop["id"])
    return gone


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
        state, tid, verdict = decisions.answer_of(con, prop)
        if state == "none":
            return False, f"proposal {pid} has no decision to check - raise it again"
        if state == "open":
            return False, (f"the founder has not answered T{tid} yet. It is on "
                           f"`hl`, top panel.")
        if state == "discarded":
            why = (db.get_task(con, tid)["discard_reason"] or "").strip()
            return False, (f"T{tid}, the decision on proposal {pid}, was discarded{f' ({why})' if why else ''} or handed back or closed by someone but the founder - "
                           f"nothing was decided. Raise it again.")
        row = decisions.card_of(con, tid)
        call = decisions.call_of(verdict)
        if call in ("approved", "rejected"):
            why_not = decisions.refusal("trust", pid, tid, subject_of(prop), row["id"] if row else None)
            if why_not or decisions.get("trust", pid)["call"] != call:
                return False, why_not or (f"the founder's recorded answer for proposal {pid} "
                                          f"does not match T{tid}'s answer")
        if call == "rejected":
            prop["state"] = "rejected"
            prop["verdict"] = textsafe.oneline(verdict or "")
            _save_proposal(prop)
            return False, f"proposal {pid} was rejected: {(verdict or '').strip()[:120]}"
        if call != "approved":
            return False, (f"T{tid} was answered "
                           f"{textsafe.oneline((verdict or '').strip())[:60]!r}, "
                           f"which is not a decision.")
        # What gets added is exactly what the founder read: the path on the
        # request row's first line, which this file cannot edit, must be the
        # proposal's root - a substring test let a proposal edited to a
        # parent directory through.
        read = _root_read(row["body"], row["requester"])
        if read is None or _resolved(read) != _resolved(prop["root"]):
            return False, (f"proposal {pid}'s root does not match what #{row['id']} "
                           f"asked the founder to approve - raise it again")
        if is_declared(prop["root"]):
            settle_moot()
            return False, f"proposal {pid} is moot: {prop['root']} is already a declared root"
        rows = list_roots()
        rows.append({"root": prop["root"], "why": prop["why"],
                     "added_by": prop["requested_by"], "added_at": prop["created"]})
        try:
            _save(rows)
        except PermissionError as e:
            return False, f"{e}. The founder's session applies it when they answer"
        prop["state"] = "applied"
        prop["applied"] = int(time.time())
        prop["verdict"] = textsafe.oneline(verdict or "")
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
    try:
        _save(kept)
    except PermissionError as e:
        return False, str(e)
    return True, f"{resolved} is no longer a declared root"
