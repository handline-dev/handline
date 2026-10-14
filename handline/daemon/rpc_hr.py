"""hr.list and the hr topic: the HR proposals as the spec's Proposal, what waits for the founder and what settled lately, and `hr.raised` /
`hr.settled` when one is raised or leaves open.

The proposals are the files of the HR store (`team/hr.py`), not rows: the wire's state is a map of the store's (proposed = open, approved =
apply_failed, applied = approved), a proposal the founder made directly (requested_by human) is never listed, and what an agent wrote
(reason, the asked fields) is text and is never markup. A proposal's id on the wire is the integer its hex id reads as."""
from __future__ import annotations

import threading
import time

from .. import db
from ..infra import paths, textsafe
from ..team import ask as askmod
from ..team import hr, proposals as hrstore
from . import agentview, rpc, stream

STATES = {"proposed": "open", "approved": "apply_failed", "applied": "approved", "rejected": "rejected", "withdrawn": "withdrawn"}
WAITING = ("open", "apply_failed")
RECENT_S, RECENT_MAX = 7 * 24 * 3600, 50
ERROR_MAX = 400


def num(pid) -> int | None:
    """The wire's id of a store id (hex text)."""
    try:
        return int(str(pid), 16)
    except ValueError:
        return None


def _settled_at(prop: dict, state: str) -> int | None:
    if state == "open":
        return None
    return int(prop.get("applied") or prop.get("decided") or prop.get("created") or 0) if state == "approved" else int(prop.get("decided") or prop.get("created") or 0)


def _items(prop: dict) -> list[dict]:
    return hr._ordered(hr._items_of(prop)) if prop["kind"] == hrstore.BUNDLE else [prop]


def title_of(prop: dict) -> str:
    if prop["kind"] == hrstore.BUNDLE:
        return f"bundle {len(_items(prop))} changes"
    return f"{prop['kind']} {prop['agent']}" + (f" -> {prop['to']}" if prop["kind"] in hr.EDGE_KINDS and prop.get("to") else "")


def _change_lines(item: dict) -> list[str]:
    """What approving one change does, in words, a line for each thing it changes."""
    kind, agent, to = item["kind"], item["agent"], item.get("to")
    if kind == "link":
        return [f"allow {agent} to ask {to} (response {item.get('response') or 'async'}" + (f", relation {item['relation']}" if item.get("relation") else "") + ")"]
    if kind == "unlink":
        return [f"remove the edge {agent} -> {to}"]
    if kind == "add":
        return [f"register {agent}" + "".join(f", {k} {item[k]}" for k in ("team", "repo", "model", "effort", "vendor") if item.get(k))]
    if kind == "team":
        return [f"create team {agent}" + (f" (working in {item['repo']})" if item.get("repo") else "") + (f" with members {', '.join(hr._members_of(item))}" if hr._members_of(item) else "")]
    if kind == "settings":
        return [f"set {k} of {agent} to {v or '(none)'}" for k, v in hr._sets_of(item).items()] or [f"change the settings of {agent}"]
    if kind == "context":
        return [f"replace {fn} of {agent}" for fn in hr._sets_of(item)] or [f"replace the standing context of {agent}"]
    if kind == "rename":
        return [f"rename {agent} to {to}"]
    if kind == "retire":
        return [f"retire {agent}"]
    return [f"{kind} {agent}"]


def _diff(prop: dict) -> str:
    return "\n".join(line for item in _items(prop) for line in _change_lines(item))


def _asked(prop: dict) -> dict | None:
    if prop["kind"] == hrstore.BUNDLE:
        return None
    out = {k: str(prop[k]) for k in ("vendor", "model", "effort", "team", "repo", "to") if prop.get(k)}
    if prop["kind"] == "settings" and hr._sets_of(prop):
        out["sets"] = hr._sets_of(prop)
    return out or None


def wire(prop: dict) -> dict | None:
    """The Proposal for a stored one; None for one that is never listed (a human's own change, or a state the wire has no word for)."""
    state = STATES.get(prop.get("state"))
    if state is None or prop.get("requested_by") == askmod.HUMAN:
        return None
    pid = num(prop["id"])
    if pid is None:
        return None
    bundle = prop["kind"] == hrstore.BUNDLE
    out = {"id": pid, "agent": f"{len(_items(prop))} changes" if bundle else prop["agent"], "kind": prop["kind"], "title": title_of(prop), "diff": _diff(prop),
           "state": state, "created": agentview._ts(int(prop.get("created") or 0)), "subject": hr.subject_of(prop),
           "settled": agentview._ts(_settled_at(prop, state)) if state != "open" else None,
           "error": textsafe.oneline(str(prop["error"]))[:ERROR_MAX] if state == "apply_failed" and prop.get("error") else None}
    if prop.get("requested_by"):
        out["requested_by"] = str(prop["requested_by"])
    if prop.get("why"):
        out["reason"] = str(prop["why"])
    asked = _asked(prop)
    if asked:
        out["asked"] = asked
    return out


def proposals(state: str | None = None, now: int | None = None) -> list[dict]:
    """What hr.list serves: the waiting ones, oldest first, then the settled of the last 7 days (at most 50), newest first; with `state`, only that, in
    the same order and with no bound."""
    now = int(time.time()) if now is None else now
    stored = [(p, wire(p)) for p in hrstore.listing()]
    items = [(p, w) for p, w in stored if w is not None]
    waiting = sorted((x for x in items if x[1]["state"] in WAITING), key=lambda x: (int(x[0].get("created") or 0), x[1]["id"]))
    settled = sorted((x for x in items if x[1]["state"] not in WAITING), key=lambda x: (-_settled_at(x[0], x[1]["state"]), -x[1]["id"]))
    if state is not None:
        return [w for _, w in waiting + settled if w["state"] == state]
    recent = [x for x in settled if _settled_at(x[0], x[1]["state"]) >= now - RECENT_S][:RECENT_MAX]
    return [w for _, w in waiting + recent]


def _list(ctx, params) -> dict:
    state = params.get("state")
    if state is not None and not isinstance(state, str):
        raise rpc.bad_request("state is text", "state", rpc.INVALID_PARAMS)
    return {"proposals": proposals(state)}


def refusal(gate: str, why: str, prop: dict) -> rpc.RpcError:
    """A refusal that carries the proposal as it is, for the card to be read again."""
    return rpc.RpcError(rpc.REFUSED, "refused", why, reason=why, gate=gate, proposal=wire(prop))


# ---------------------------------------------------------------- the hr topic
_lock = threading.Lock()
_seen: dict[int, tuple] | None = None       # wire id -> (state, settled, error) as last looked at; None until the first look


def check(con) -> None:
    """Say what changed in the store since the last look, once: a proposal that appeared open is `hr.raised`; one that left open (decided, withdrawn)
    or whose apply ran again is `hr.settled`. The first look says nothing: what was there before is the snapshot's."""
    global _seen
    now = {w["id"]: w for w in (wire(p) for p in hrstore.listing()) if w is not None}
    with _lock:
        before, _seen = _seen, {i: (w["state"], w["settled"], w["error"]) for i, w in now.items()}
        if before is None:
            return
        for i, w in sorted(now.items()):
            was = before.get(i)
            key = (w["state"], w["settled"], w["error"])
            if was == key:
                continue
            if was is None and w["state"] == "open":
                kind = "hr.raised"
            elif w["state"] == "open":
                continue
            else:
                kind = "hr.settled"
            stream.emit(con, "hr", kind, {"proposal": w}, agent=None if w["kind"] == hrstore.BUNDLE else w["agent"], title=w["title"],
                        target={"screen": "hr", "id": str(i)})


def signature() -> tuple:
    """Whether the store changed, for a watcher that otherwise looks only at the database."""
    d = paths.home() / "hr"
    try:
        return tuple(sorted((f.name, f.stat().st_mtime_ns) for f in d.glob("*.yaml")))
    except OSError:
        return ()


def reset() -> None:
    global _seen
    with _lock:
        _seen = None


rpc.METHODS["hr.list"] = rpc.Method(_list, "agent")
