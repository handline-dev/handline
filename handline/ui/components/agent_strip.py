from __future__ import annotations

import time
from dataclasses import dataclass, replace

from rich.text import Text
from textual.widgets import Static

from ...infra import textsafe
from ...team import agents
from ...usage import status
from . import agent_rows

BAR_CELLS = 8


@dataclass
class AgentInfo:
    """What the strip says about one agent; None is unknown."""
    model: str | None = None
    account: str | None = None
    ctx_pct: int | None = None
    ctx_used: int | None = None
    ctx_limit: int | None = None
    cache_left_s: int | None = None      # seconds until the prompt cache lapses, negative once it has


def read_info(con, agent: str, now: float | None = None) -> AgentInfo:
    """The strip's figures: the usage snapshot of the agent's own session and the life left in its prompt
    cache. Small reads only."""
    now = time.time() if now is None else now
    account = agents.current_account(agent, con)
    info = AgentInfo(account=account, cache_left_s=agent_rows.cache_left(con, agent, int(now)))
    if not account:
        return info
    st = status.read_for_agent(agent, account)
    info.model, info.ctx_pct, info.ctx_used = st.model_id, st.trusted_ctx_pct, st.trusted_ctx_used
    try:
        info.ctx_limit = status.effective_context_limit(agents.read(agent), st.model_id)
    except Exception:       # noqa: BLE001 - an unreadable config leaves the limit unknown
        pass
    return info


def _tokens(n: int) -> str:
    return f"{n / 1000:.0f}k" if n >= 1000 else str(n)


def strip_text(info: AgentInfo) -> Text:
    """model · account · context bar % used/limit · time left in the prompt cache."""
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
        ctx,
        Text(agent_rows.cache_text(info.cache_left_s), style="yellow" if info.cache_left_s is not None and 0 < info.cache_left_s < 60 else ""),
    ]
    out = Text(no_wrap=True, overflow="ellipsis", style="dim")
    for i, p in enumerate(parts):
        out.append(" · " if i else "")
        out.append_text(p)
    return out


class AgentStrip(Static):
    """The one line under a chat's header with the agent's model, account, context and the time left in its
    prompt cache, which counts down each second between reads. Always shown on the agent's page, in every tab."""

    DEFAULT_CSS = "AgentStrip { height: 1; padding: 0 2; }"

    def on_mount(self) -> None:
        self._info: AgentInfo | None = None
        self._read_at = 0.0
        self.set_interval(1, self._tick)

    def show(self, con, agent: str) -> None:
        self._read_at = time.time()
        self._info = read_info(con, agent, self._read_at)
        self.update(strip_text(self._info))

    def _tick(self) -> None:
        info = self._info
        if info is None or info.cache_left_s is None:
            return
        self.update(strip_text(replace(info, cache_left_s=info.cache_left_s - int(time.time() - self._read_at))))
