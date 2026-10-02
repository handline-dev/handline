"""What an agent's settings are and how they are written: the one place the chat
page's Settings tab, the HR tab and `ocp agent set` all read and write them."""
from __future__ import annotations

from pathlib import Path

from .. import actions
from ..usage import status
from ..infra import textsafe
from . import agents

EDITABLE = ("model", "effort", "weight", "repo", "skip_permissions", "browser", "browser_sites",
            "context_k", "idle_close", "handoff_note")
BROWSER_MODES = (("off", "off"), ("Claude in Chrome", "chrome"), ("sandboxed Playwright", "playwright"))
BROWSER_CUSTOM = ("custom, set in a terminal", "custom")   # shown only when the agent has one
WEIGHTS = ("light", "heavy")
HANDOFF_NOTES = (("follow the global switch", "default"), ("on", "on"), ("off", "off"))


def settings_of(agent: str) -> dict:
    """An agent's configuration as the settings tab shows it: what its agent
    file says, and the account its session is actually on."""
    cfg = agents.read(agent)
    return {"model_id": status.read_for_agent(agent, agents.current_account(agent)).model_id or "",
            "session_account": agents.current_account(agent) or "",
            "vendor": str(cfg.get("vendor") or agents.DEFAULT_VENDOR),
            "enabled": bool(cfg.get("enabled", True)),
            "model": str(cfg.get("model") or agents.DEFAULT_MODEL),
            "effort": str(cfg.get("effort") or agents.DEFAULT_EFFORT),
            "weight": str(cfg.get("weight") or "light"), "repo": str(cfg.get("repo") or ""),
            "skip_permissions": "on" if agents.skip_permissions_in_effect(agent, cfg) else "off",
            "browser": agents.browser_mode(cfg), "browser_sites": " ".join(agents.browser_origins(cfg)),
            "context_k": str(agents.context_tokens(cfg) // 1000),
            "handoff_note": {True: "on", False: "off"}.get(cfg.get("handoff_note"), "default"),
            "idle_close": actions.idle_close_minutes(cfg["park_timeout_s"])
            if isinstance(cfg.get("park_timeout_s"), int) and not isinstance(cfg.get("park_timeout_s"), bool) else ""}


def settings_problem(form: dict) -> str | None:
    """Why `form` cannot be saved, or None. Checked here so nothing is written
    that the agent file would refuse to load again."""
    model, repo = form["model"].strip(), form["repo"].strip()
    if not model:
        return "model cannot be empty"
    if textsafe.has_unsafe(model) or not agents.is_plain_text(model):
        return f"model {model!r} would not be read back as text"
    if form["effort"] not in agents.VALID_EFFORT:
        return f"effort must be one of {', '.join(agents.VALID_EFFORT)}"
    if form["weight"] not in WEIGHTS:
        return f"weight must be one of {', '.join(WEIGHTS)}"
    if form["skip_permissions"] not in ("on", "off"):
        return "skip permissions must be on or off"
    if form["browser"] not in (*dict(BROWSER_MODES).values(), BROWSER_CUSTOM[1]):
        return "browser must be off, chrome or playwright"
    if form["browser"] == "playwright":
        try:
            if not agents.parse_origins(form["browser_sites"]):
                return "name at least one site for the sandboxed browser"
        except ValueError as e:
            return str(e)
    if form["handoff_note"] not in dict(HANDOFF_NOTES).values():
        return "handoff note must be on, off or default"
    if not form["context_k"].strip().isdigit() or int(form["context_k"]) < 1:
        return "context must be a whole number of thousands of tokens, at least 1"
    if form["idle_close"].strip() and not form["idle_close"].strip().isdigit():
        return "idle close must be a whole number of minutes (0 = never), or blank to follow the global time"
    if repo and (textsafe.has_unsafe(repo) or not Path(repo).expanduser().is_dir()):
        return f"repo {repo!r} is not a folder"
    return None


def settings_changes(current: dict, form: dict) -> list[tuple[str, str, str]]:
    """(field, old, new) for each editable field the form changes. A blank repo
    means leave it: the agent file cannot be told to forget one."""
    out = []
    for key in EDITABLE:
        new = form[key].strip()
        if key == "repo" and not new:
            continue
        if key == "browser_sites":
            if form["browser"] != "playwright":      # Claude in Chrome has no site list
                continue
            new = " ".join(agents.parse_origins(new))
        if new != current[key]:
            out.append((key, current[key], new))
    return out


def save_settings(agent: str, changes: list[tuple[str, str, str]]) -> tuple[bool, str]:
    """Write `changes` to the agent's file through the same call `ocp agent
    register` uses, which keeps every other field as it is. A running session
    is not restarted."""
    new = {k: v for k, _, v in changes}
    idle = new.pop("idle_close", None)
    note = new.pop("handoff_note", None)
    if idle is not None:
        try:
            actions.parse_idle_close(idle)      # a bad one is refused before the file is written
        except ValueError as e:
            return False, f"not saved: {e}"
    if new:
        try:
            chrome = mcp = None
            if "browser" in new or "browser_sites" in new:
                cfg = agents.read(agent)
                mode = new.get("browser") or agents.browser_mode(cfg)
                origins = agents.parse_origins(new["browser_sites"]) if "browser_sites" in new \
                    else agents.browser_origins(cfg)
                chrome, mcp = mode == "chrome", agents.browser_mcp_config(cfg, mode, origins)
            agents.register(agent, model=new.get("model"), effort=new.get("effort"),
                            weight=new.get("weight"), repo=new.get("repo"),
                            skip_permissions=None if "skip_permissions" not in new else new["skip_permissions"] == "on",
                            chrome=chrome, mcp_config=mcp,
                            context_tokens=int(new["context_k"]) * 1000 if "context_k" in new else None)
        except (ValueError, agents.ConfigError, OSError) as e:
            return False, f"not saved: {e}"
    if note is not None:
        try:
            cfg = agents.read(agent)
            if note == "default":
                cfg.pop("handoff_note", None)
            else:
                cfg["handoff_note"] = note == "on"
            agents.write(agent, cfg)
        except (ValueError, agents.ConfigError, OSError) as e:
            return False, f"handoff note not saved: {e}"
    if idle is not None:
        ok, said = actions.set_idle_close(idle, agent)
        if not ok:
            done = ", ".join(f"{k} {v}" for k, v in new.items())
            return False, f"idle close not saved: {said}" + (f" ({done} was saved)" if done else "")
    return True, "saved: " + ", ".join(f"{k} {v}" for k, _, v in changes)


# The one split between what an agent may set on its own and what raises what it
# may do: only these apply when an agent asks; every other field, and any field
# added later, waits for the founder in HR.
ORDINARY = frozenset({"model", "effort", "context_k", "handoff_note"})


def privileged(field: str) -> bool:
    return field not in ORDINARY


def form_with(agent: str, values: dict[str, str]) -> tuple[dict, dict]:
    """(current settings, the form once `values` are put over them). ValueError
    for a field that is not a setting."""
    unknown = sorted(set(values) - set(EDITABLE))
    if unknown:
        raise ValueError(f"not a setting: {', '.join(unknown)} (settings: {', '.join(EDITABLE)})")
    current = settings_of(agent)
    return current, {**{k: current[k] for k in EDITABLE}, **{k: str(v) for k, v in values.items()}}


NOTHING = "nothing to change"


def changes_for(agent: str, values: dict[str, str]) -> tuple[list[tuple[str, str, str]], str]:
    """(what `values` would change, why it cannot be saved or ""): checked the same
    way the Settings tab checks its form."""
    try:
        current, form = form_with(agent, values)
        problem = settings_problem(form)
        if problem:
            return [], problem
        changes = [c for c in settings_changes(current, form) if c[0] in values]
    except (ValueError, OSError, agents.ConfigError) as e:
        return [], str(e)
    return changes, "" if changes else f"{NOTHING}: it is already so"


def set_values(agent: str, values: dict[str, str]) -> tuple[bool, str]:
    """Write `values` through the register path, which only the founder's own
    session may take."""
    changes, problem = changes_for(agent, values)
    if problem:
        return False, f"not saved: {problem}"
    return save_settings(agent, changes)


def set_by_agent(agent: str, values: dict[str, str], speaker: str | None) -> tuple[bool, str]:
    """An agent's own change, applied for it by the daemon: only its own, and only
    what `ORDINARY` names. `speaker` is the caller's own word, as for ask and say: the
    socket is private to the user, so this guards against mistakes, not a hostile local process."""
    if speaker != agent:
        return False, f"{speaker or 'someone'} may not change {agent}'s settings; an agent changes its own"
    held = sorted(k for k in values if privileged(k))
    if held:
        return False, f"{', '.join(held)} needs the founder's approval: propose it with `ocp agent set`"
    return set_values(agent, values)
