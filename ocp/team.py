"""team.yaml routing policy (team model sections 2-4, 6).

An agent only ever knows a target and a message. Everything else - whether the
edge is allowed, the intake mode, the priority, the account, what happens to
the requester - is decided here, deterministically, with no model involved
(KICKOFF rule 4).

Unlisted edges are denied. That is the whole point: agents cannot spray
requests around, and they cannot infer policy or route around it.
"""
from __future__ import annotations

import fnmatch
import time
from dataclasses import dataclass

from . import paths
from .yamlcompat import ConfigError, load

DEFAULT_MAX_CHAIN_DEPTH = 4
DEFAULT_ASK_RATE_PER_HOUR = 20

VALID_INTAKE = {"serial", "ephemeral", "dedicated"}
# blocking is deliberately absent (team model section 2, axis 3): a requester
# holding an account while it waits is the most expensive idle in a system
# with a fixed number of accounts.
VALID_RESPONSE = {"async", "fire_and_forget"}


@dataclass
class Decision:
    allowed: bool
    reason: str
    relation: str | None = None
    response: str = "async"
    kind: str | None = None
    intake: str | None = None
    creates_gate: bool = False
    chain_depth: int = 0


class Team:
    def __init__(self, data: dict):
        self.teams: dict[str, list[str]] = data.get("teams") or {}
        self.agents: dict[str, dict] = data.get("agents") or {}
        self.edges: list[dict] = data.get("edges") or []
        self.deny: list[dict] = data.get("deny") or []
        limits = data.get("limits") or {}
        self.max_chain_depth = int(limits.get("max_chain_depth", DEFAULT_MAX_CHAIN_DEPTH))
        self.ask_rate_per_hour = int(limits.get("ask_rate_per_hour", DEFAULT_ASK_RATE_PER_HOUR))
        self._validate()

    # ------------------------------------------------------------ loading
    @classmethod
    def load(cls, path=None) -> "Team":
        p = path or paths.team_file()
        if not p.exists():
            raise FileNotFoundError(f"{p} not found - run `ocp team init`")
        data = load(p.read_text())
        if not isinstance(data, dict):
            raise ConfigError(f"{p}: top level is not a map")
        return cls(data)

    def _validate(self) -> None:
        for name, cfg in self.agents.items():
            if not isinstance(cfg, dict):
                raise ConfigError(f"agents.{name} is not a map")
            intake = cfg.get("intake", "dedicated")
            if intake not in VALID_INTAKE:
                raise ConfigError(
                    f"agents.{name}.intake={intake!r} is not one of {sorted(VALID_INTAKE)}"
                )
        for i, e in enumerate(self.edges):
            if not isinstance(e, dict):
                raise ConfigError(f"edges[{i}] is not a map")
            if "from" not in e or "to" not in e:
                raise ConfigError(f"edges[{i}] needs both 'from' and 'to'")
            resp = e.get("response", "async")
            if resp not in VALID_RESPONSE:
                raise ConfigError(
                    f"edges[{i}].response={resp!r} is not one of {sorted(VALID_RESPONSE)}. "
                    "'blocking' is intentionally not supported."
                )

    # ------------------------------------------------------------ patterns
    def members(self, group: str) -> list[str]:
        return list(self.teams.get(group, []))

    def team_of(self, agent: str) -> str | None:
        for t, members in self.teams.items():
            if agent in (members or []):
                return t
        return None

    def matches(self, pattern: str, agent: str) -> bool:
        """'*' matches anything, 'eng.*' matches members of team eng, otherwise
        the pattern is an agent name (glob allowed)."""
        if pattern == "*":
            return True
        if pattern.endswith(".*"):
            return agent in self.members(pattern[:-2])
        if pattern == agent:
            return True
        return fnmatch.fnmatchcase(agent, pattern)

    def known(self, agent: str) -> bool:
        """Is this a routable target?

        Besides declared agents and team members, a literal (non-wildcard)
        'to' in an edge counts. That is how escalation targets like
        'orchestrator' exist: they are routable without being agents that we
        launch and account for.
        """
        if agent in self.agents or self.team_of(agent) is not None:
            return True
        return any(
            str(e.get("to", "")) == agent
            for e in self.edges
            if "*" not in str(e.get("to", ""))
        )

    def intake_of(self, agent: str) -> str:
        return (self.agents.get(agent) or {}).get("intake", "dedicated")

    # ------------------------------------------------------------ routing
    def route(self, requester: str, target: str, chain_depth: int = 0) -> Decision:
        if requester == target:
            return Decision(False, "an agent cannot ask itself")
        if not self.known(target):
            return Decision(False, f"unknown target '{target}'")

        if chain_depth >= self.max_chain_depth:
            return Decision(
                False,
                f"chain depth {chain_depth} reached the limit {self.max_chain_depth} "
                "(loop guard)",
            )

        # Explicit denies win over any allow, so a deny cannot be shadowed by
        # an earlier wildcard edge.
        for d in self.deny:
            if self.matches(str(d.get("from", "")), requester) and \
               self.matches(str(d.get("to", "")), target):
                why = d.get("reason") or "denied by policy"
                return Decision(False, f"denied: {requester} -> {target}: {why}")

        # First match wins, like firewall rules. Order in team.yaml is
        # significant: the design's own policy depends on it, e.g. a verdict
        # from `reviewer -> *` stays async even when a later `* -> archivist`
        # edge would have made it fire_and_forget.
        for e in self.edges:
            if self.matches(str(e["from"]), requester) and self.matches(str(e["to"]), target):
                return Decision(
                    True,
                    f"edge {e['from']} -> {e['to']}",
                    relation=e.get("relation"),
                    response=e.get("response", "async"),
                    kind=e.get("kind"),
                    intake=self.intake_of(target),
                    creates_gate=bool(e.get("creates_gate")),
                    chain_depth=chain_depth,
                )

        return Decision(
            False,
            f"denied: no edge declares {requester} -> {target} "
            "(unlisted edges are denied by default)",
        )

    def rate_limited(self, con, requester: str) -> tuple[bool, str]:
        from . import db

        used = db.asks_since(con, requester, int(time.time()) - 3600)
        if used >= self.ask_rate_per_hour:
            return True, f"rate limit: {used} asks in the last hour >= {self.ask_rate_per_hour}"
        return False, f"{used}/{self.ask_rate_per_hour} asks this hour"


STARTER = """# team.yaml - routing policy. Unlisted edges are denied.
teams:
  eng: [backend]
  ops: [archivist]

agents:
  backend:   { intake: dedicated }
  reviewer:  { intake: serial }
  archivist: { intake: ephemeral }

edges:
  - { from: "*", to: "reviewer", relation: peer, response: async }
  - { from: "reviewer", to: "*", kind: verdict, response: async }
  - { from: "*", to: "archivist", relation: peer, response: fire_and_forget }

deny: []

limits:
  max_chain_depth: 4
  ask_rate_per_hour: 20
"""
