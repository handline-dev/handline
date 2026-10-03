from __future__ import annotations

import time
from dataclasses import dataclass

from rich.text import Text
from textual.widgets import Static

from ... import db
from ...infra import textsafe, timefmt
from ...team import agents
from ...usage import prices, status

CACHE_CALLS = 10      # the cache hit rate is read over this many of the session's latest calls
BAR_CELLS = 8


@dataclass
class AgentInfo:
    """What the strip says about one agent; None is unknown."""
    model: str | None = None
    account: str | None = None
    session_age_s: float | None = None
    ctx_pct: int | None = None
    ctx_used: int | None = None
    ctx_limit: int | None = None
    cache_hit: float | None = None
    last_cost: float | None = None


def read_info(con, agent: str, now: float | None = None) -> AgentInfo:
    """The strip's figures: the usage snapshot of the agent's own session, its latest session start, and the
    session's recorded API calls (`token_calls`). Small reads only."""
    now = time.time() if now is None else now
    account = agents.current_account(agent, con)
    info = AgentInfo(account=account)
    starts = [r for r in db.session_starts(con, agent, 5) if not r["resumed"]]
    if starts:
        info.session_age_s = max(0.0, now - starts[-1]["ts"])
    if not account:
        return info
    st = status.read_for_agent(agent, account)
    info.model, info.ctx_pct, info.ctx_used = st.model_id, st.trusted_ctx_pct, st.trusted_ctx_used
    try:
        info.ctx_limit = status.effective_context_limit(agents.read(agent), st.model_id)
    except Exception:       # noqa: BLE001 - an unreadable config leaves the limit unknown
        pass
    if st.session_id:
        calls = con.execute(
            "SELECT model, inp, cc, cr, out, thinking FROM token_calls WHERE account = ? AND session = ? "
            "AND side = 0 ORDER BY ts DESC, req DESC LIMIT ?", (account, st.session_id, CACHE_CALLS)).fetchall()
        read = sum(c["cr"] for c in calls)
        total = read + sum(c["cc"] + c["inp"] for c in calls)
        info.cache_hit = read / total if total else None
        if calls:
            last = calls[0]
            info.last_cost = prices.cost(last["model"], last["inp"], last["cc"], last["cr"], last["out"],
                                         last["thinking"])
    return info


def _tokens(n: int) -> str:
    return f"{n / 1000:.0f}k" if n >= 1000 else str(n)


def strip_text(info: AgentInfo) -> Text:
    """model · account · session age · context bar % used/limit · cache hit % · last call cost."""
    ctx = Text("ctx ")
    if info.ctx_pct is None:
        ctx.append("?", style="dim")
    else:
        full = round(info.ctx_pct / 100 * BAR_CELLS)
        ctx.append("▇" * full, style="yellow" if info.ctx_pct >= 80 else "green")
        ctx.append("░" * (BAR_CELLS - full), style="dim")
        ctx.append(f" {info.ctx_pct}%")
        if info.ctx_used is not None and info.ctx_limit:
            ctx.append(f" {_tokens(info.ctx_used)}/{_tokens(info.ctx_limit)}", style="dim")
    parts = [
        Text(textsafe.oneline(info.model or "model ?")),
        Text(textsafe.oneline(info.account or "no account")),
        Text("session " + (timefmt.span(info.session_age_s) if info.session_age_s is not None else "?")),
        ctx,
        Text("cache " + (f"{info.cache_hit:.0%}" if info.cache_hit is not None else "?")),
        Text("last " + (f"${info.last_cost:.2f}" if info.last_cost is not None else "?")),
    ]
    out = Text(no_wrap=True, overflow="ellipsis", style="dim")
    for i, p in enumerate(parts):
        out.append(" · " if i else "")
        out.append_text(p)
    return out


class AgentStrip(Static):
    """The one line under a chat's header with the agent's model, account, session age, context, cache and
    last call cost. Hidden where no single agent is chatted with."""

    DEFAULT_CSS = "AgentStrip { height: 1; padding: 0 2; }"

    def show(self, con, agent: str) -> None:
        self.update(strip_text(read_info(con, agent)))
