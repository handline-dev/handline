"""team.yaml routing policy (team model sections 2-4, 6).

An agent only ever knows a target and a message. Everything else - whether the
edge is allowed, the priority, the account, what happens to
the requester - is decided here, deterministically, with no model involved
(AGENTS.md rule 4).

Unlisted edges are denied. That is the whole point: agents cannot spray
requests around, and they cannot infer policy or route around it.
"""
from __future__ import annotations

import fnmatch
import time
from dataclasses import dataclass

from ..infra import paths, sealed
from ..infra.yamlcompat import ConfigError, load

# The links an agent has without any edge written for them, so nobody runs
# `hl hr link` by hand for the ordinary shape. The name is the structure: the
# parent of `harbor.sea` is `harbor` (everything before the last dot), a name
# without a dot has none. Grandparents and nephews are not reachable by default.
# A team's manager is the member named like the team (team `harbor` is
# [harbor, harbor.sea, ...]). Explicit `deny` entries still win over these, and
# an edge in team.yaml is found first; `hr link` / `hr unlink` are for the links
# between teams and the other exceptions.
DEFAULT_LINKS = (
    "an agent may ask its parent (its name up to the last dot), its children and its siblings",
    "a manager may ask the founder (a decision task)",
)
HUMAN = "human"


def parent_name(agent: str) -> str | None:
    """The agent a name hangs under: `harbor.sea` -> `harbor`, `a.b.c` -> `a.b`; None for a
    name with no dot."""
    head, dot, _ = agent.rpartition(".")
    return head if dot and head else None

DEFAULT_MAX_CHAIN_DEPTH = 4
DEFAULT_ASK_RATE_PER_HOUR = 20

# blocking is deliberately absent (team model section 2, axis 3): a requester
# holding an account while it waits is the most expensive idle in a system
# with a fixed number of accounts.
VALID_RESPONSE = {"async", "fire_and_forget"}


# The ask limiter's window. Rolling, not a bucket that empties on the hour: a
# slot frees exactly one of these after the ask that took it, and an agent told
# to "wait for the window" waits an hour for what was minutes away.
ASK_WINDOW_S = 3600


@dataclass
class Decision:
    allowed: bool
    reason: str
    relation: str | None = None
    response: str = "async"
    kind: str | None = None
    chain_depth: int = 0


def _edge_kind(kind):
    """An edge's kind as a Decision carries it. `escalate` (T628) is the old spelling of a request
    to the founder, which is a task of theirs; a team.yaml that still says it keeps working."""
    return "task" if kind == "escalate" else kind


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
            raise FileNotFoundError(f"{p} not found - run `hl team init`")
        text = p.read_text()
        sealed.check(sealed.TEAM, text, p)
        data = load(text)
        if not isinstance(data, dict):
            raise ConfigError(f"{p}: top level is not a map")
        return cls(data)

    def _validate(self) -> None:
        for name, cfg in self.agents.items():
            if not isinstance(cfg, dict):
                raise ConfigError(f"agents.{name} is not a map")
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
            self._check_pattern(f"edges[{i}].from", str(e["from"]))
            self._check_pattern(f"edges[{i}].to", str(e["to"]))

        # deny needs the same scrutiny as edges, and more: an edge that fails to
        # parse denies by default, but a deny that fails to parse silently
        # *allows*. A typo'd key, a misspelt team or a missing 'to' would each
        # turn "explicit denies always win" into "this deny does nothing".
        for i, d in enumerate(self.deny):
            if not isinstance(d, dict):
                raise ConfigError(
                    f"deny[{i}] is not a map: {d!r}. "
                    "An unquoted value like 'bug#42' can do this."
                )
            missing = [k for k in ("from", "to") if k not in d]
            if missing:
                raise ConfigError(
                    f"deny[{i}] is missing {', '.join(missing)} (keys present: "
                    f"{', '.join(sorted(d)) or 'none'}). A deny without both "
                    "matches nothing and would silently allow."
                )
            self._check_pattern(f"deny[{i}].from", str(d["from"]))
            self._check_pattern(f"deny[{i}].to", str(d["to"]))

    def _check_pattern(self, where: str, pattern: str) -> None:
        """A '<team>.*' pattern naming a team that does not exist matches
        nothing, so it would silently allow. Catch it at load time."""
        if pattern.endswith(".*") and pattern != "*":
            group = pattern[:-2]
            if group not in self.teams:
                raise ConfigError(
                    f"{where}={pattern!r} refers to team {group!r}, which is not in "
                    f"teams ({', '.join(sorted(self.teams)) or 'none'})"
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
        'to' in an edge counts. That is how the founder's name
        'human' exists: they are routable without being agents that we
        launch and account for.
        """
        if agent in self.agents or self.team_of(agent) is not None:
            return True
        return any(
            str(e.get("to", "")) == agent
            for e in self.edges
            if "*" not in str(e.get("to", ""))
        )

    def managers(self) -> dict[str, str]:
        """Team name -> its manager, the member named like the team."""
        return {t: t for t, ms in self.teams.items() if t in (ms or [])}

    def default_link(self, requester: str, target: str) -> Decision | None:
        """The link `DEFAULT_LINKS` gives this pair, or None."""
        def hit(why: str, **kw) -> Decision:
            return Decision(True, f"default link: {why}", **kw)

        mine, theirs = parent_name(requester), parent_name(target)
        if theirs == requester:
            return hit(f"{requester} is {target}'s parent", relation="supervisor")
        if mine == target:
            return hit(f"{requester} is {target}'s child", relation="subordinate")
        if mine is not None and mine == theirs:
            return hit(f"{requester} and {target} are siblings under {mine}", relation="peer")
        if target == HUMAN and requester in self.managers():
            return hit(f"{requester} may ask the founder", kind="task")
        return None

    # ------------------------------------------------------------ routing
    def route(self, requester: str, target: str, chain_depth: int = 0,
              reply: bool = False) -> Decision:
        """`reply`: this answers a request `target` sent to `requester`. A reply needs no
        edge back - with resolve gone it is the only way to answer - but an explicit deny
        still wins."""
        if requester == target:
            return Decision(False, "an agent cannot ask itself")
        if not self.known(target) and not (target == HUMAN and self.default_link(requester, target)):
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
                    kind=_edge_kind(e.get("kind")),
                    chain_depth=chain_depth,
                )

        default = self.default_link(requester, target)
        if default is not None:
            default.chain_depth = chain_depth
            return default

        if reply:
            return Decision(True, f"reply: {target} asked {requester}", response="fire_and_forget",
                            chain_depth=chain_depth)

        return Decision(
            False,
            f"denied: no edge declares {requester} -> {target} "
            "(unlisted edges are denied by default)",
        )

    def rate_limited(self, con, requester: str) -> tuple[bool, str]:
        from .. import db

        used = db.asks_since(con, requester, int(time.time()) - ASK_WINDOW_S)
        if used >= self.ask_rate_per_hour:
            return True, f"rate limit: {used} asks in the last hour >= {self.ask_rate_per_hour}"
        return False, f"{used}/{self.ask_rate_per_hour} asks this hour"


STARTER = """# team.yaml - routing policy. Unlisted edges are denied.
teams:
  eng: [backend]
  ops: [archivist]

agents:
  backend:   {}
  reviewer:  {}
  archivist: {}

edges:
  - { from: "*", to: "reviewer", relation: peer, response: async }
  - { from: "reviewer", to: "*", kind: verdict, response: async }
  - { from: "*", to: "archivist", relation: peer, response: fire_and_forget }
  # The founder. Not an agent - no account, no session, no terminal - but a
  # routable name, so an agent has somewhere to send a decision only a person
  # can make, and the person has a way back down.
  - { from: "*", to: "human", kind: task }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }

deny: []

limits:
  max_chain_depth: 4
  ask_rate_per_hour: 20
"""
