"""What a screen shows about an agent or account, and the settings it edits."""
from __future__ import annotations

import shlex
import time

from ..team import accounts
from ..infra import timefmt
from ..team import agents
from ..usage import collect
from .. import db
from ..usage import load as loadmod
from ..session import orca
from ..infra import paths
from ..team import spawn as spawnmod
from ..usage import status
from ..infra import textsafe
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
    untold = [Entry(r["id"], r["target"], db.shown_verdict(r) or r["state"], r["resolved_at"] or r["ts"])
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
            "cost": status.live_cost(acct), "cap": loadmod.cap_of(acct), "agents": rows}


def set_account_cap(acct: str, text: str) -> tuple[bool, str]:
    """The most agents mid-turn on an account at once, from what was typed."""
    try:
        n = int(text.strip())
        loadmod.set_cap(acct, n)
    except ValueError:
        return False, "a cap is a whole number, at least 1"
    return True, f"{acct}: at most {n} agents mid-turn"


def save_account_settings(acct: str, label: str, cap: str) -> tuple[bool, str]:
    """The account page's Settings form: its alias and its cap, each written only when it
    differs from what is there. Nothing is written if either is refused."""
    label = label.strip()
    if textsafe.has_unsafe(label) or len(label) > 40:
        return False, "an alias is one plain line of at most 40 characters"
    try:
        n = int(cap.strip())
    except ValueError:
        n = 0
    if n < 1:
        return False, "the cap is a whole number, at least 1"
    now = loadmod.label_of(acct)
    said = []
    if label != ("" if now == acct else now):
        loadmod.set_label(acct, label)
        said.append(f"alias {label}" if label else "alias cleared")
    if n != loadmod.cap_of(acct):
        loadmod.set_cap(acct, n)
        said.append(f"{acct}: at most {n} agents mid-turn")
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
    return True, f"default cap {n}"


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


def trust_worktrees(acct: str, worktrees: list[tuple[str, str]]) -> list[str]:
    """Pre-authorize `acct` for each (agent, worktree), one throwaway session
    at a time, so a real session there never meets the trust screen cold."""
    return [_trust_one_worktree(acct, name, wt) for name, wt in worktrees]


def _trust_one_worktree(acct: str, agent: str, worktree: str) -> str:
    drv = vendors.for_account(acct)
    cmd = (f"cd {shlex.quote(worktree)} && {drv.config_dir_env()}="
           f"{shlex.quote(str(vendors.config_dir(acct)))} {shlex.quote(drv.binary() or drv.name)}")
    handle = orca.create_terminal(cmd, title=f"trust-{acct}-{agent}")
    if not handle:
        return f"{agent} ({worktree}): could not start a session to trust it"
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
                return f"{agent} ({worktree}): {why}"
            orca.wait_idle(handle)
            result = f"{agent} ({worktree}): trusted"
        else:
            result = f"{agent} ({worktree}): no trust prompt appeared - already trusted?"
        orca.send(handle, "/exit")
        return result
    finally:
        orca.close_terminal(handle)
