"""What the home page's AGENTS rows say beyond `tui.agents_data`: the short model, the
prompt-cache time left, and every reason the founder should look at the agent - a
dialog or block, a delivery that is not moving, a usage limit, an open ask - said on
the agent's own row, and rolled up onto the fold that hides it."""
from __future__ import annotations

import time
from typing import NamedTuple

from ... import actions, agents, blocked, daemon, db, load as loadmod, textsafe, tui


class Extra(NamedTuple):
    kind_short: str
    cache_left: int | None          # seconds of prompt cache left; None with no activity on record
    block: str                      # what stops ocp reaching it, once that is on the founder's list
    stuck: str                      # why it is not moving, or its usage limit; or ""
    asks: int                       # its open asks to the founder
    urgency: tuple[str, int, str] | None   # what the reports a fold hides need: (glyph, n, colour)


def kind_short(cfg: dict) -> str:
    """The model without its provider prefix (`zai-coding-plan/glm-5.3-flash` is
    `glm-5.3-flash`) and its effort, no vendor: `agents.kind` for a narrow column."""
    _vendor, model, effort = agents.parts(cfg)
    return f"{model.rsplit('/', 1)[-1]} {agents.EFFORT_SHORT.get(effort, effort)}"


def cache_left(con, agent: str, now: int | None = None) -> int | None:
    """Seconds until this agent's prompt cache lapses, negative once it has: it lives
    PROMPT_CACHE_S from the agent's last activity, the clock `daemon.cache_expired` uses."""
    last = db.last_activity(con, agent)
    if last is None:
        return None
    return daemon.PROMPT_CACHE_S - ((int(time.time()) if now is None else now) - last)


def cache_cell(left: int | None) -> str:
    """ANSI: `58m`, then `m:ss` in the last ten minutes, yellow from there; `gone` once
    the cache has lapsed, `·` with nothing to measure from."""
    if left is None:
        return f"{tui.DIM}·{tui.R}"
    if left <= 0:
        return f"{tui.DIM}gone{tui.R}"
    if left >= 600:
        return f"{left // 60}m"
    return f"{tui.YEL}{left // 60}:{left % 60:02d}{tui.R}"


def founder_block(con, agent: str, now: float | None = None):
    """The delivery block on `agent` that is on the founder's list - old enough to be
    said (`show_at`) - or None."""
    b = db.block_get(con, agent)
    return b if b and (time.time() if now is None else now) >= b["show_at"] else None


def _stuck_by_agent(con) -> dict[str, dict]:
    """`actions.stuck`'s rows by the agent each is about, the first of each."""
    out: dict[str, dict] = {}
    for b in actions.stuck(con):
        if b["agent"]:
            out.setdefault(b["agent"], b)
    return out


def fold_urgency(con, names: list[str], stuck: dict) -> tuple[str, int, str] | None:
    """The most urgent thing among agents a fold hides: (glyph, how many share it,
    colour). ✉ is an open ask to the founder, ! a dialog, a block, a stuck delivery or a
    limit (red when it is an error), ▶ work going on; None when there is nothing to say."""
    found: dict[int, list[str]] = {}
    for name in names:
        row = db.get_agent_state(con, name)
        if con.execute("SELECT 1 FROM requests WHERE requester = ? AND target = ? "
                       "AND state = 'queued'", (name, actions.HUMAN)).fetchone():
            found.setdefault(0, []).append(tui.YEL)
            continue
        word, _ = tui.screen_state(con, row)
        if word in tui.NEEDS_A_HUMAN or founder_block(con, name):
            found.setdefault(1, []).append(tui.RED)
        elif name in stuck or daemon.wall_reason(con, name, row):
            found.setdefault(1, []).append(tui.YEL)
        elif loadmod.is_running(row):
            found.setdefault(2, []).append(tui.STATE_COLOR["RUNNING"])
    if not found:
        return None
    worst = min(found)
    colours = found[worst]
    return ("▶" if worst == 2 else "✉" if worst == 0 else "!"), len(colours), \
        tui.RED if tui.RED in colours else colours[0]


def collect(con, rows: list[tui.AgentRow]) -> dict[str, Extra]:
    """The extras for each row that has cells, by agent name."""
    stuck = _stuck_by_agent(con)
    hidden = {n: h for n, _depth, h in tui._walk_tree()}
    out: dict[str, Extra] = {}
    for r in rows:
        if r.error:
            continue
        try:
            _acct, cfg = agents.placed(r.name, con)
        except Exception:  # noqa: BLE001
            cfg = {}
        fb = founder_block(con, r.name)
        block = textsafe.oneline(blocked.headline(fb["kind"], fb["detail"]))[:tui.DETAIL_CAP] if fb else ""
        limit = daemon.wall_reason(con, r.name, db.get_agent_state(con, r.name))
        why = textsafe.oneline(stuck[r.name]["why"] if r.name in stuck else limit or "")
        asks = con.execute("SELECT COUNT(*) FROM requests WHERE requester = ? AND target = ? "
                           "AND state = 'queued'", (r.name, actions.HUMAN)).fetchone()[0]
        out[r.name] = Extra(textsafe.oneline(kind_short(cfg)), cache_left(con, r.name), block,
                            why.removeprefix(f"{r.name} is ")[:tui.DETAIL_CAP], asks,
                            fold_urgency(con, hidden.get(r.name, []), stuck) if r.under else None)
    return out
