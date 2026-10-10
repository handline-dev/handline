"""What the home page's AGENTS rows say beyond `viewdata.agents_data`: the short model, the
prompt-cache time left, and every reason the founder should look at the agent - a
dialog or block, a delivery that is not moving, a usage limit, an open ask - said on
the agent's own row, and rolled up onto the fold that hides it."""
from __future__ import annotations

import re
import time
from typing import NamedTuple

from ...infra.yamlcompat import ConfigError
from ... import actions, db
from ...team import agents
from ...session import blocked, idle_close
from ...usage import load as loadmod
from ...infra import textsafe
from ...view import viewdata
from ...daemon import delivery as dmn_delivery, handoff_note
from ...daemon.screenstate import DETAIL_CAP


class Extra(NamedTuple):
    kind_short: str
    cache_left: int | None          # seconds of prompt cache left; None with no activity on record
    block: str                      # what stops ocp reaching it, once that is on the founder's list
    stuck: str                      # why it is not moving, or its usage limit; or ""
    asks: int                       # its open asks to the founder
    urgency: tuple[str, int, str] | None   # what the reports a fold hides need: (glyph, n, colour)
    cache_ignored: bool = False     # its resume ignores cache expiry, so the countdown means nothing


def kind_short(cfg: dict) -> str:
    """The model without its provider prefix (`zai-coding-plan/glm-5.3-flash` is
    `glm-5.3-flash`) and its effort, no vendor: `agents.kind` for a narrow column."""
    _vendor, model, effort = agents.parts(cfg)
    return f"{model.rsplit('/', 1)[-1]} {agents.EFFORT_SHORT.get(effort, effort)}"


def cache_left(con, agent: str, now: int | None = None) -> int | None:
    """Seconds until this agent's prompt cache lapses, negative once it has: its real life off
    its last response when the transcript says (an hour, or five minutes after an overage), else
    an hour from the agent's last activity, the clock `daemon.cache_expired` falls back to."""
    left = handoff_note.cache_left(con, agent, int(time.time()) if now is None else now)
    return None if left is None else int(left)


def cache_text(left: int | None) -> str:
    """`cache 3:20` counting down (`1:05:00` while an hour's life is over its first hour), `cache expired`
    once it has run out, `cache ?` with nothing to measure from."""
    if left is None:
        return "cache ?"
    if left <= 0:
        return "cache expired"
    h, rest = divmod(left, 3600)
    m, sec = divmod(rest, 60)
    return f"cache {h}:{m:02d}:{sec:02d}" if h else f"cache {m}:{sec:02d}"


def cache_cell(left: int | None, ignored: bool = False) -> str:
    """ANSI: `58m`, then `m:ss` in the last ten minutes, yellow from there; `-` once
    the cache has lapsed or with nothing to measure from; `ignored` when the agent
    resumes its session whatever the cache did."""
    if ignored:
        return f"{viewdata.DIM}ignored{viewdata.R}"
    if left is None:
        return f"{viewdata.DIM}-{viewdata.R}"
    if left <= 0:
        return f"{viewdata.DIM}-{viewdata.R}"
    if left >= 600:
        return f"{left // 60}m"
    return f"{viewdata.YEL}{left // 60}:{left % 60:02d}{viewdata.R}"


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
            found.setdefault(0, []).append(viewdata.YEL)
            continue
        word, _ = viewdata.screen_state(con, row)
        if word in viewdata.NEEDS_A_HUMAN or founder_block(con, name):
            found.setdefault(1, []).append(viewdata.RED)
        elif name in stuck or dmn_delivery.wall_reason(con, name, row):
            found.setdefault(1, []).append(viewdata.YEL)
        elif loadmod.is_running(row):
            found.setdefault(2, []).append(viewdata.STATE_COLOR["RUNNING"])
    if not found:
        return None
    worst = min(found)
    colours = found[worst]
    return ("▶" if worst == 2 else "✉" if worst == 0 else "!"), len(colours), \
        viewdata.RED if viewdata.RED in colours else colours[0]


# What a reason says after its point is made: the row it sits on already shows the agent and the key that fixes it.
_NOTE_TAILS = (" - Orca no longer has it", " - nothing can be typed to it")


def agent_note(name: str, why: str) -> str:
    """`why` as the note on `name`'s own row: without the agent's name, which the row already shows, and
    without the tail that only repeats the point."""
    for lead in (f"{name}'s ", f"{name} is ", f"{name} "):
        if why.startswith(lead):
            why = why[len(lead):]
            break
    own = re.escape(name)       # a whole name: an agent called "a" is not the a in "a terminal"
    why = re.sub(rf"(?<![\w.-]){own}'s ", "its ", why)
    why = re.sub(rf"(?<![\w.-]){own}(?![\w.-])", "it", why)
    for tail in _NOTE_TAILS:
        why = why.replace(tail, "")
    return why


def collect(con, rows: list[viewdata.AgentRow]) -> dict[str, Extra]:
    """The extras for each row that has cells, by agent name."""
    stuck = _stuck_by_agent(con)
    hidden = {n: h for n, _depth, h in viewdata._walk_tree()}
    out: dict[str, Extra] = {}
    for r in rows:
        if r.error:
            continue
        try:
            cfg = agents.read(r.name)
        except (OSError, ConfigError):
            cfg = {}
        fb = founder_block(con, r.name)
        block = textsafe.oneline(blocked.headline(fb["kind"], fb["detail"]))[:DETAIL_CAP] if fb else ""
        limit = dmn_delivery.wall_reason(con, r.name, db.get_agent_state(con, r.name))
        why = textsafe.oneline(stuck[r.name]["why"] if r.name in stuck else limit or "")
        asks = con.execute("SELECT COUNT(*) FROM requests WHERE requester = ? AND target = ? "
                           "AND state = 'queued'", (r.name, actions.HUMAN)).fetchone()[0]
        out[r.name] = Extra(textsafe.oneline(kind_short(cfg)), cache_left(con, r.name), block,
                            agent_note(r.name, why), asks,
                            fold_urgency(con, hidden.get(r.name, []), stuck) if r.under else None,
                            idle_close.ignore_cache_expiry(r.name))
    return out
