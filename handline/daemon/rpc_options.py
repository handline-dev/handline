"""agent.options: what the new-agent form offers, read from what exists: the repos the agents work in, the roles of the
agents already on the team (a name and the first line of its ROLE.md, to start a new one from), the models and efforts
`hl agent set` accepts, the accounts and whether each is logged in, and the names team.yaml declares that no agent has yet.
No hl command serves it. What the spec leaves open is read as: `roles` is one entry per registered agent (its name, and the first
line of its ROLE.md that is not its own title), `free_names` the names team.yaml declares that no agent has yet, `models` and
`efforts` what `hl agent set` accepts, `login_state` `ok`, `needs_login` or `unknown` as the account's credential reads."""
from __future__ import annotations

from ..infra import paths
from ..team import accounts, agent_settings, agents
from ..team import team as teammod
from . import rpc, rpc_worktrees

LOGIN = {True: "ok", False: "needs_login", None: "unknown"}


def _role_line(name: str) -> str:
    try:
        text = (paths.agent_dir(name) / "ROLE.md").read_text()
    except OSError:
        return ""
    lines = [ln.strip().lstrip("#").strip() for ln in text.splitlines() if ln.strip()]
    return (lines[1] if len(lines) > 1 and lines[0].lower().startswith(name.lower()) else lines[0] if lines else "")[:200]


def _free_names() -> list[str]:
    try:
        declared = teammod.Team.load().agents
    except Exception:  # noqa: BLE001 - no team.yaml, or one that does not verify: nothing is declared
        return []
    return sorted(set(declared) - set(paths.list_agents()))


def _options(ctx, params) -> dict:
    return {"repos": rpc_worktrees.repos(),
            "roles": [{"name": n, "description": _role_line(n)} for n in paths.list_agents()],
            "models": agent_settings.known_models(), "efforts": list(agents.VALID_EFFORT),
            "accounts": [{"name": a, "login_state": LOGIN[accounts.login_state(a)]} for a in paths.list_accounts()],
            "free_names": _free_names()}


rpc.METHODS["agent.options"] = rpc.Method(_options, "member")
