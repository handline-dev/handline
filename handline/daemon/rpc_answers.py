"""answers.list, answer.add and answer.remove: the founder's standing answers, the folders trusted for every session and the rules that
answer a vendor's dialog (the Home settings; `hl trust` is the command for the folders). They live in the protected store, which no
agent can write, so these are the founder's alone.

The stores are files, so an effect and its `write_effects` record cannot be one transaction. A retry is told apart instead: the call
leaves an intent (a cursor named for its caller and request_id) before it writes and clears it once it has recorded its answer, so a
retry that finds the intent knows its first attempt may have got as far as the write, and takes "already so" for the outcome it
wanted: a folder that is trusted, a rule that is saved, an answer that is gone. A first attempt is judged strictly."""
from __future__ import annotations

from .. import actions, db
from ..actions import dialogs
from . import rpc

_ADD_FIELDS = ("folder", "vendor", "title", "label")


def _intent(ctx, params, name: str) -> str:
    return f"intent:{name}:{ctx.caller.actor}:{params['request_id']}"


def _begun(ctx, params, name: str) -> bool:
    """Leave the intent; True when it was already there (a retry of a call that did not finish)."""
    con = db.connect()
    try:
        key = _intent(ctx, params, name)
        again = db.cursor_updated(con, key) is not None
        db.set_cursor(con, key, 1)
        return again
    finally:
        con.close()


def _finish(ctx, params, name: str, said: str, *, ok: bool = True) -> None:
    """The call is over: record its answer, if it succeeded, and drop the intent either way."""
    con = db.connect()
    try:
        if ok:
            db.record_effect(con, ctx.caller.actor, params["request_id"], name, {"accepted": True, "said": said})
        db.clear_cursor(con, _intent(ctx, params, name))
    finally:
        con.close()


def _answers(ctx, params) -> dict:
    return {"answers": [{"kind": a["kind"], "key": list(a["key"]) if isinstance(a["key"], (list, tuple)) else [a["key"]], "text": a["text"]}
                        for a in actions.standing_answers()]}


def _add(ctx, params) -> dict:
    given = {}
    for k in _ADD_FIELDS:
        v = params.get(k)
        if v is not None and not isinstance(v, str):
            raise rpc.bad_request(f"{k} is text", k, rpc.INVALID_PARAMS)
        given[k] = (v or "").strip()
    if not any(given.values()):
        raise rpc.bad_request("give a folder to trust, or the vendor, title and label of a rule", "folder", rpc.INVALID_PARAMS)
    retry = _begun(ctx, params, "answer.add")
    ok, said = actions.add_answer(**given)
    if not ok and retry and ("already" in said):
        ok = True       # what the first attempt wanted is so
    if not ok:
        _finish(ctx, params, "answer.add", said, ok=False)
        raise rpc.refused(said)
    _finish(ctx, params, "answer.add", said)
    return {"accepted": True, "said": said}


def _remove(ctx, params) -> dict:
    kind, key = params.get("kind"), params.get("key")
    if kind not in (dialogs.TRUST, "dialog"):
        raise rpc.bad_request("kind is trust or dialog", "kind", rpc.INVALID_PARAMS)
    if not isinstance(key, list) or not all(isinstance(k, str) and k for k in key) or len(key) != (1 if kind == dialogs.TRUST else 3):
        raise rpc.bad_request("key is [folder] for trust and [vendor, title, label] for dialog", "key", rpc.INVALID_PARAMS)
    retry = _begun(ctx, params, "answer.remove")
    ok, said = actions.remove_answer(kind, key[0] if kind == dialogs.TRUST else key)
    gone = not ok and ("no rule" in said or "not a declared root" in said)
    if gone and retry:
        ok, said = True, f"{' / '.join(key)} is already removed"
    if not ok:
        _finish(ctx, params, "answer.remove", said, ok=False)
        if gone:
            raise rpc.RpcError(rpc.NOT_FOUND, "not_found", said, what="answer", name=" / ".join(key))
        raise rpc.refused(said)
    _finish(ctx, params, "answer.remove", said)
    return {"accepted": True, "said": said}


rpc.METHODS["answers.list"] = rpc.Method(_answers, "founder")
rpc.METHODS["answer.add"] = rpc.Method(_add, "founder", write=True)
rpc.METHODS["answer.remove"] = rpc.Method(_remove, "founder", write=True)
