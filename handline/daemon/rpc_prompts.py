"""prompt.list and prompt.answer: the permission prompt, question or dialog an agent's screen shows now, and the answer
to the one the caller saw. They read the screen through `actions.prompt_blocks`, which the
legacy `answer_block` verb already answers through; no hl command serves them, they are the IDE's and web UI's.

An answer names the prompt by its id, the screen block's fingerprint. When the open prompt is another, nothing is sent
and the reply is `stale_prompt` with the current one. `choice` is one of the prompt's option keys, or `{text}` for
the free-text answer to a question that has an Other row."""
from __future__ import annotations

from .. import actions, db
from ..actions import prompt_blocks
from ..infra import paths
from . import inputhold, rpc, rpc_agents, rpc_gates


def _agent(params) -> str:
    name = params.get("agent")
    if not isinstance(name, str) or not name:
        raise rpc.bad_request("agent is a name", "agent", rpc.INVALID_PARAMS)
    if name not in paths.list_agents():
        raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no such agent: {name}", what="agent", name=name)
    return name


def _list(ctx, params) -> dict:
    name = _agent(params)
    con = db.connect()
    try:
        block = actions.open_block_of(con, name)
        return {"prompts": [rpc_agents.prompt_of(block)] if block else []}
    finally:
        con.close()


def _answer_for(block: dict, choice) -> dict:
    """What `prompt_blocks.answer` takes for `choice` on this block; bad_request for one the prompt does not offer."""
    kind = block["kind"]
    if isinstance(choice, list):
        if not (block.get("detail") or {}).get("multi_select"):
            raise rpc.bad_request("only a multi prompt takes an array of keys", "choice", rpc.INVALID_PARAMS)
        offered = {o["key"] for o in rpc_agents.options_of(block)} - {"esc"}
        bad = [k for k in choice if k not in offered]
        if bad or len(set(choice)) != len(choice):
            raise rpc.bad_request(f"this prompt offers {', '.join(sorted(offered)) or 'no choice'}, each at most once", "choice", rpc.INVALID_PARAMS)
        return {"choices": [int(k) - 1 for k in choice], "text": None}
    if isinstance(choice, dict):
        text = choice.get("text")
        if set(choice) != {"text"} or not isinstance(text, str) or not text:
            raise rpc.bad_request("choice is an option key, or {text} with the text", "choice", rpc.INVALID_PARAMS)
        if kind != prompt_blocks.QUESTION or not (block["detail"].get("other_number")):
            raise rpc.bad_request("this prompt takes no text answer", "choice", rpc.INVALID_PARAMS)
        return {"choices": [], "text": text}
    if not isinstance(choice, str):
        raise rpc.bad_request("choice is an option key, or {text} with the text", "choice", rpc.INVALID_PARAMS)
    offered = {o["key"] for o in rpc_agents.options_of(block)}
    if choice not in offered:
        raise rpc.bad_request(f"this prompt offers {', '.join(sorted(offered)) or 'no choice'}, not {choice!r}", "choice", rpc.INVALID_PARAMS)
    if choice == "esc":
        return {"cancel": True}
    if kind == prompt_blocks.PERMISSION:
        return {"yes": choice == "yes"}
    if kind == prompt_blocks.QUESTION:
        return {"choices": [int(choice) - 1]}
    return {"number": int(choice), "always": False}


def _answer(ctx, params) -> dict:
    prompt, choice = params.get("prompt"), params.get("choice")
    if not isinstance(prompt, str) or not prompt:
        raise rpc.bad_request("prompt is the id of the prompt the caller saw", "prompt", rpc.INVALID_PARAMS)
    if isinstance(choice, list):
        if not all(isinstance(k, str) for k in choice):
            raise rpc.bad_request("choice is an array of option keys", "choice", rpc.INVALID_PARAMS)
    elif not isinstance(choice, (str, dict)):
        raise rpc.bad_request("choice is an option key, or {text} with the text", "choice", rpc.INVALID_PARAMS)
    name = _agent(params)
    rpc_gates.terminal_lock(name)
    con = db.connect()
    try:
        rpc_gates.input_holder(ctx, con, name)
        block = actions.open_block_of(con, name)
        if block is None or block["fingerprint"] != prompt:
            raise rpc.RpcError(rpc.STALE_PROMPT, "stale_prompt", "the prompt on screen is not the one answered; nothing was sent",
                               current=rpc_agents.prompt_of(block) if block else None)
        answer = _answer_for(block, choice)
        ok, said = prompt_blocks.answer(con, block["id"], answer)
        if not ok:
            raise rpc.refused(said)
        inputhold.touch(con, name, ctx.caller.info["name"])
        return {"accepted": True, "said": said}
    finally:
        con.close()


rpc.METHODS["prompt.list"] = rpc.Method(_list, "member")
rpc.METHODS["prompt.answer"] = rpc.Method(_answer, "member", write=True)
