"""What a screen shows about an agent or account, and the settings it edits."""
from __future__ import annotations

import shlex
import threading
import time

from ..team import accounts
from ..infra import timefmt
from ..team import agents
from ..usage import collect
from .. import db
from ..usage import load as loadmod
from ..session import idle_close, orca
from ..infra import paths
from ..team import spawn as spawnmod
from ..usage import status
from ..infra import textsafe
from ..infra.yamlcompat import ConfigError
from ..session import triggers
from ..team import trust
from .. import vendors

from .conversation import CHAT_TAB, Entry, Event, Fact, OTHERS_TAB, UNTOLD_TAB, chat_unread, conversation_and_drawn


def agent_detail(con, agent: str, hint: bool = True) -> list[tuple[str, list[str]]]:
    """What is behind an agent's row, in sections a screen can draw.

    The head, then the conversation the human has with it, then whatever is
    waiting on it or owed to it that is not part of that conversation - a
    request from another agent is not something the human said to it. What it
    knows and what it has done are read on request (`agent_knows`, `agent_done`).

    `hint`: see `conversation_and_drawn`.
    """
    row = db.get_agent_state(con, agent)
    try:
        cfg = agents.read(agent)
        acct = agents.current_account(agent, con)
    except Exception as e:  # noqa: BLE001
        return [("", [f"config error: {e}"])]
    st = status.read_for_agent(agent, acct)
    head = [Fact(("account", acct or "-"), ("model", str(cfg.get("model"))),
                 ("effort", cfg.get("effort") or agents.DEFAULT_EFFORT),
                 ("ctx", "?" if st.ctx_pct is None else str(st.ctx_pct) + "%"))]
    if row:
        closed = "  ·closed" if row["closed_at"] else ""
        head.append(Fact(("state", f"{row['state']}{closed}"),
                         *([("wakes", timefmt.stamp(row["wake_at"], timefmt.CLOCK))]
                           if row["wake_at"] else [])))
        if row["detail"]:
            head.append(row["detail"])

    # What the conversation drew is not repeated; every other queued request
    # and untold answer is listed under it, including one in a chain too old
    # for the conversation to draw.
    chat, drawn = conversation_and_drawn(con, agent, hint)
    others = [Entry(r["id"], r["requester"], r["body"] or "", r["ts"])
              for r in db.queued_for(con, agent) if r["id"] not in drawn]
    untold = [Entry(r["id"], r["target"], db.shown_verdict(r) or r["state"], db.answered_at(r))
              for r in db.unannounced_answers(con, agent) if r["id"] not in drawn]
    if others or untold:
        # Where the reader is left, at the end of the conversation.
        chat += [f"below: {len(others)} also queued for it, {len(untold)} "
                 "answers it has not been told - press G to read them"]
    sections = [("", head)]
    n = chat_unread(con, agent)
    sections.append((CHAT_TAB + (f" ({n if n < 100 else '99+'} new)" if n else ""), chat))
    if others:
        sections.append((f"{OTHERS_TAB} ({len(others)})", others))
    if untold:
        sections.append((f"{UNTOLD_TAB} ({len(untold)})", untold))
    return sections


def agent_knows(con, agent: str) -> list[str]:
    """The handoff an agent would be resumed with."""
    kept = db.get_handoff(con, agent)
    return kept.splitlines() if kept else ["no handoff waiting - it has not been parked"]


def agent_done(con, agent: str) -> list:
    """The last events recorded for an agent, newest first."""
    recent = [Event(e["ts"], e["kind"], e["payload"] or "")
              for e in con.execute(
                  "SELECT ts, kind, payload FROM events WHERE agent = ? "
                  "ORDER BY id DESC LIMIT 12", (agent,)).fetchall()]
    return recent or ["nothing recorded yet"]


def agent_where(con, agent: str) -> str:
    """The terminal handle, and the session it would resume if closed - a
    technical id, never on the head where a person reads at a glance, but
    still one keystroke away for the one thing it is for: an agent stuck on
    a screen only the handle can reach."""
    row = db.get_agent_state(con, agent)
    if not row:
        return f"{agent} has never been started"
    said = f"terminal {row['terminal'] or 'none'}"
    if row["closed_at"]:
        said += f"  ·  resumes session {row['resume_session'] or 'unknown'}"
    return said


def agent_worktree(con, agent: str) -> str:
    """Where this agent's repo lives on disk - off the head for the same
    reason as the terminal handle: nothing a person types, only reads when
    they need to go there themselves."""
    row = db.get_agent_state(con, agent)
    if not row or not row["worktree"]:
        return f"{agent} has no worktree"
    return spawnmod.worktree_path(row["worktree"]) or row["worktree"]


def _config_line(acct: str) -> str:
    try:
        return f"{vendors.config_dir(acct)} ({vendors.account_vendor(acct)})"
    except vendors.UnknownVendor as e:
        return str(e)


def account_facts(con, acct: str, logged_in=None) -> dict:
    """What is known of an account, as values, for the bridge's account page.

    `logged_in` is the caller's login check: the bridge redraws the page every
    couple of seconds and passes its cached one, since the real one asks the
    keychain.
    """
    logged_in = logged_in or accounts.logged_in
    st = status.read(acct)
    loads = loadmod.read_all(con)
    # Ranked for this account's own vendor: the page holds an account, not an
    # agent, and claude's ranking has one thing to say about another vendor's
    # account - that it is not claude's.
    _, ranked = status.pick_account([a for a in paths.list_accounts() if logged_in(a)],
                                    load=loads, vendor=vendors.account_vendor(acct))
    is_in = logged_in(acct)
    allowed, why = status.account_verdict(acct, st, loads.get(acct), ranked, is_in)
    rows = []
    for a in paths.list_agents():
        if agents.current_account(a, con) == acct:
            row = db.get_agent_state(con, a)
            rows.append((a, (row["state"] if row else "never started")
                         + (" ·closed" if row and row["closed_at"] else ""),
                         (row["detail"] if row else "") or ""))
    return {"status": st, "logged_in": is_in, "config": _config_line(acct),
            "vendor": vendors.account_vendor(acct), "assign": (allowed, why),
            "cost": status.live_cost(acct), "cap": loadmod.cap_of(acct), "own_cap": loadmod.own_cap(acct),
            "recover": loadmod.usage_recover_enabled(acct),
            "default_cap": loadmod.default_cap(), "agents": rows}


def set_account_cap(acct: str, text: str) -> tuple[bool, str]:
    """The most agents mid-turn on an account at once, from what was typed."""
    try:
        n = int(text.strip())
        loadmod.set_cap(acct, n)
    except ValueError:
        return False, "a cap is a whole number, at least 1"
    except ConfigError as e:
        return False, str(e)
    return True, f"{acct}: at most {n} agents mid-turn"


def save_account_settings(acct: str, label: str, cap: str,
                          recover: bool | None = None) -> tuple[bool, str]:
    """The account page's Settings form: its alias, its cap and (when given) the usage
    recovery switch, each written only when it differs from what is there. A blank
    cap writes none, so an account with no cap of its own keeps following the
    default. Nothing is written if any is refused."""
    label = label.strip()
    if textsafe.has_unsafe(label) or len(label) > 40:
        return False, "an alias is one plain line of at most 40 characters"
    n = None
    if cap.strip():
        try:
            n = int(cap.strip())
        except ValueError:
            n = 0
        if n < 1:
            return False, "the cap is a whole number, at least 1"
    now = loadmod.label_of(acct)
    said = []
    try:
        if label != ("" if now == acct else now):
            loadmod.set_label(acct, label)
            said.append(f"alias {label}" if label else "alias cleared")
        if n is not None and n != loadmod.own_cap(acct):
            loadmod.set_cap(acct, n)
            said.append(f"{acct}: at most {n} agents mid-turn")
        if recover is not None and recover != loadmod.usage_recover_enabled(acct):
            loadmod.set_usage_recover(acct, recover)
            said.append(f"usage recovery {'on' if recover else 'off'}")
    except ConfigError as e:
        return False, str(e)
    return True, "; ".join(said) or "nothing changed"


def context_share(k_text: str, model: str) -> str:
    """What a handoff threshold typed in thousands of tokens comes to on this model's
    window, for the agent Settings tab to show beside the field."""
    try:
        k = int(k_text.strip())
    except ValueError:
        return ""
    if k < 1:
        return ""
    size = collect.ctx_size(model.strip())
    if not size:
        return "window unknown for this model"
    held = min(k * 1000, int(size * triggers.CONTEXT_CLAMP_FRACTION))
    shown = f"{round(held * 100 / size)}% of the {size // 1000}k window"
    return shown if held == k * 1000 else f"held to {shown}"


def set_default_cap(text: str) -> tuple[bool, str]:
    """The cap of every account that has none of its own, from what was typed."""
    try:
        n = int(text.strip())
        loadmod.set_default_cap(n)
    except ValueError:
        return False, "the default cap is a whole number, at least 1"
    except ConfigError as e:
        return False, str(e)
    return True, f"default cap {n}"


def idle_close_minutes(seconds: int) -> str:
    """The idle close time as the settings show it: whole minutes, "0" for never."""
    return str(-(-seconds // 60))


def parse_idle_close(text: str) -> int | None:
    """Seconds from the minutes typed: 0 is never; blank is None (an agent's: use the global)."""
    text = text.strip()
    if not text:
        return None
    if not text.isdigit():
        raise ValueError("the idle close time is a whole number of minutes, 0 for never")
    return int(text) * 60


def set_idle_close(text: str, agent: str | None = None) -> tuple[bool, str]:
    """The idle close time, from the minutes typed: the global one, or `agent`'s own (blank drops it)."""
    try:
        seconds = parse_idle_close(text)
        if agent:
            idle_close.set_agent_close_timeout(agent, seconds)
        elif seconds is None:
            return False, "the idle close time is a whole number of minutes, 0 for never"
        else:
            idle_close.set_close_timeout(seconds)
    except (ValueError, ConfigError, OSError) as e:
        return False, str(e)
    if seconds is None:
        return True, f"{agent}: idle close follows the global time"
    return True, f"idle close {'never' if seconds == idle_close.NEVER else idle_close_minutes(seconds) + ' min'}"


# The on/off switches of the global settings: (key, label, what it does). Each reads and writes
# the one place the daemon reads it from.
GLOBAL_SWITCHES = (
    ("handoff_note", "handoff note", "agents write a note for their next session before a close or a move"),
    ("auto_accept", "auto-accept", "ocp accepts a dialog on an agent's terminal when it has been stuck on it"),
    ("ignore_cache_expiry", "ignore cache expiry", "a resume reuses the session even past its prompt cache's life"),
    ("dev_mode", "dev mode", "the daemon restarts itself when ocp's own code changes"),
)


def _switch_accessors() -> dict:
    from ..daemon import devmode, handoff_note
    from ..session import autoaccept
    return {"handoff_note": (handoff_note.default_enabled, handoff_note.set_default_enabled),
            "auto_accept": (autoaccept.enabled, autoaccept.set_enabled),
            "ignore_cache_expiry": (idle_close.default_ignore_cache_expiry, idle_close.set_ignore_cache_expiry),
            "dev_mode": (devmode.dev_mode_enabled, devmode.set_dev_mode)}


def global_switches() -> dict[str, bool]:
    """Where each global switch stands now."""
    return {k: get() for k, (get, _) in _switch_accessors().items()}


def save_global_switches(values: dict[str, bool]) -> tuple[bool, str]:
    """Write the switches that differ from where they stand; the rest are left alone."""
    now, changed = global_switches(), []
    try:
        for key, (_, put) in _switch_accessors().items():
            if key in values and bool(values[key]) != now[key]:
                put(bool(values[key]))
                changed.append(f"{key.replace('_', ' ')} {'on' if values[key] else 'off'}")
    except (ConfigError, OSError) as e:
        return False, str(e)
    return True, ", ".join(changed)


def eligible_worktrees() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Every registered agent's worktree, split (declared, undeclared).

    A y/N that presses "Yes" to whatever it finds means what it says only
    when it lists exactly what it will press it for - so a worktree outside
    a declared root is never included, only reported as skipped.
    """
    con = db.connect()
    try:
        found = []
        for name in paths.list_agents():
            row = db.get_agent_state(con, name)
            wt = spawnmod.worktree_path(row["worktree"]) if row else None
            if wt:
                found.append((name, wt))
    finally:
        con.close()
    declared = [(a, w) for a, w in found if trust.is_declared(w)]
    undeclared = [(a, w) for a, w in found if not trust.is_declared(w)]
    return declared, undeclared


def trust_each(acct: str, worktrees: list[tuple[str, str]]) -> list[tuple[bool, str]]:
    """Pre-authorize `acct` for each (agent, worktree), one throwaway session
    at a time, so a real session there never meets the trust screen cold.
    One (ok, line) per worktree."""
    return [_trust_one_worktree(acct, name, wt) for name, wt in worktrees]


def trust_worktrees(acct: str, worktrees: list[tuple[str, str]]) -> tuple[bool, str]:
    """`trust_each` as (ok when none failed, the lines joined by "; ")."""
    done = trust_each(acct, worktrees)
    return all(ok for ok, _ in done), "; ".join(line for _, line in done)


def _trust_one_worktree(acct: str, agent: str, worktree: str) -> tuple[bool, str]:
    drv = vendors.for_account(acct)
    cmd = (f"cd {shlex.quote(worktree)} && {drv.config_dir_env()}="
           f"{shlex.quote(str(vendors.config_dir(acct)))} {shlex.quote(drv.binary() or drv.name)}")
    handle = orca.create_terminal(cmd, title=f"trust-{acct}-{agent}")
    if not handle:
        return False, f"{agent} ({worktree}): could not start a session to trust it"
    try:
        # Booting, not mid-turn: `wait_idle`'s probe wants a running session,
        # and this one has none yet, so a bounded poll for the one screen
        # this cares about instead.
        deadline = time.time() + 20
        while time.time() < deadline and not orca.trust_prompt_showing(handle):
            if orca.idle_now(handle):
                break
            time.sleep(1)
        if orca.trust_prompt_showing(handle):
            ok, why = orca.confirm_trust(handle)
            if not ok:
                return False, f"{agent} ({worktree}): {why}"
            orca.wait_idle(handle)
            result = f"{agent} ({worktree}): trusted"
        else:
            result = f"{agent} ({worktree}): no trust prompt appeared - already trusted?"
        orca.send(handle, "/exit")
        return True, result
    finally:
        orca.close_terminal(handle)


_SAMPLES: dict = {"db": None, "floor": None, "stamp": None, "rows": [], "ts": []}
_SAMPLES_BUCKET_S = 300
_SAMPLES_LOCK = threading.Lock()        # the outlook loads on worker threads, and two can overlap


def _usage_samples(con, since: int) -> list:
    """The usage samples from `since` on, oldest first, read once for everything the
    stats tab asks of them in a refresh - the outlook, what each agent used, the curve
    under it, each with a slightly different `since` - and again only when a sample has
    been written since, or a later window needs older ones than were read. Unchanged
    samples are not read out of the database three times over."""
    with _SAMPLES_LOCK:
        return _usage_samples_locked(con, since)


def _usage_samples_locked(con, since: int) -> list:
    from bisect import bisect_left
    from ..usage import outlook
    from ..infra import paths
    have = _SAMPLES
    where = str(paths.db_file())
    if have["db"] == where and have["floor"] is not None and have["floor"] <= since:
        stamp = con.execute("SELECT COUNT(*), COALESCE(MAX(ts), 0) FROM usage_samples WHERE ts >= ?",
                            (have["floor"],)).fetchone()
        if tuple(stamp) == have["stamp"]:
            return have["rows"][bisect_left(have["ts"], since):]
    floor = since - since % _SAMPLES_BUCKET_S
    rows = db.usage_samples(con, since=floor)
    outlook.forget_before(floor)
    have.update(db=where, floor=floor, rows=rows, ts=[r["ts"] for r in rows],
                stamp=(len(rows), rows[-1]["ts"] if rows else 0))
    return rows[bisect_left(have["ts"], since):]


def _outlook_rows(con, now: int, hours: int) -> list:
    """The samples the graph, what each agent used and the curve under it all read: the same window,
    from just before the range starts so the first rise into it is counted (T163)."""
    from ..usage import outlook
    return _usage_samples(con, now - hours * 3600 - outlook.MAX_AGE_S)


def weekly_outlook(con, now: int, hours: int = 24):
    """The weekly headroom of all accounts together, the last `hours` and a guess at the next."""
    from ..usage import outlook
    return outlook.weekly_outlook(_outlook_rows(con, now, hours), now, hours)


def agent_use(con, now: int, hours: int) -> list:
    """Each agent's tokens, list-price cost and estimated share of the week's points over the
    last `hours`, largest first."""
    from ..usage import outlook, prices, stats
    lo = now - hours * 3600
    used = outlook.week_consumed(_outlook_rows(con, now, hours), lo, now)
    return stats.agent_use(con, lo, now, prices.table_from_config(loadmod._config()), used)


def agent_overlay(con, use, now: int, hours: int, times: list[int]) -> list[float]:
    """Points of the combined week `use` (an `agent_use` row) had eaten by each of `times`: its
    cost share of each account times what that account used so far, averaged over the accounts."""
    from ..usage import outlook
    curves = outlook.consumed_curve(_outlook_rows(con, now, hours), times)
    n = len(curves) or 1
    return [sum(use.fracs.get(a, 0.0) * c[k] for a, c in curves.items()) / n for k in range(len(times))]
