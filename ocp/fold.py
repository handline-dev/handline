"""fold - rewrite an agent's digest from the event delta (design section 5).

Three rules from the design drive the shape of this module.

Fold runs outside the session. Summarizing inside the session makes the input
grow with session length without bound, and it eats the reserve right at the
point where headroom matters most. So fold is a separate call, on a cheap
model, and it does not have to be the agent's own account.

The cursor is a row id and only advances after the output validates, inside the
same transaction that records the fold. A time-based cursor would either lose
or duplicate events that arrive while the fold is running.

A fold is a destructive rewrite. Atomic writes only prevent a half-written
file; they do not prevent a fully written garbage file from replacing a good
one. So the candidate is validated, and on failure we roll back and fall back
to a deterministic digest rather than publishing the garbage.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import accounts, agents, db, digest, paths

FOLD_TIMEOUT_S = 180
TRANSCRIPT_TAIL_BYTES = 40000


def journal_path(agent: str, day: str) -> Path:
    d = paths.agent_dir(agent) / "journal"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{day}.md"


def handoff_path(agent: str) -> Path:
    d = paths.agent_dir(agent) / "handoff"
    d.mkdir(parents=True, exist_ok=True)
    return d / "latest.md"


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _git(args: list[str], cwd: Path, strip: bool = True) -> str:
    """Run git and return stdout.

    strip=False matters for `status --porcelain`: its status field is two
    columns wide and the first can be a space, so stripping the whole output
    eats a character off the first filename.
    """
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            return ""
        return r.stdout.strip() if strip else r.stdout.rstrip("\n")
    except (OSError, subprocess.SubprocessError):
        return ""


def deterministic_facts(agent: str, events, repo: Path | None = None) -> str:
    """Facts that do not depend on any model output.

    Always attached, so that a failed fold still leaves enough to resume on
    (section 5). This is the part that must never be missing.
    """
    lines: list[str] = []
    if repo and (repo / ".git").exists():
        branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
        head = _git(["rev-parse", "--short", "HEAD"], repo)
        lines.append(f"- repo: {repo}")
        lines.append(f"- branch: {branch or '?'} @ {head or '?'}")
        log = _git(["log", "-5", "--pretty=%h %s"], repo)
        if log:
            lines.append("- recent commits:")
            lines.extend(f"    {line}" for line in log.splitlines())
        changed = _git(["status", "--porcelain"], repo, strip=False)
        if changed:
            names = [line[3:] for line in changed.splitlines()[:10] if len(line) > 3]
            lines.append(f"- uncommitted: {', '.join(names)}")
    if events:
        lines.append(f"- events folded: {len(events)} "
                     f"(ids {events[0]['id']}..{events[-1]['id']})")
        kinds: dict[str, int] = {}
        for e in events:
            kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
        lines.append("- event kinds: " + ", ".join(f"{k}x{v}" for k, v in sorted(kinds.items())))
        lines.append("- last events:")
        for e in events[-8:]:
            when = time.strftime("%H:%M", time.localtime(e["ts"]))
            payload = e["payload"] or ""
            lines.append(f"    {when} {e['kind']} {payload[:90]}")
    else:
        lines.append("- events folded: 0 (nothing new since the last fold)")
    lines.append(f"- generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    return "\n".join(lines)


def _transcript_tail(agent: str) -> str:
    """Tail of the agent's most recent transcript, if we can find one."""
    try:
        acct = agents.account_of(agent)
    except Exception:  # noqa: BLE001
        return ""
    from . import status

    st = status.read(acct)
    if not st.raw.get("transcript_path"):
        return ""
    p = Path(st.raw["transcript_path"])
    if not p.exists():
        return ""
    try:
        data = p.read_bytes()[-TRANSCRIPT_TAIL_BYTES:]
        return data.decode("utf-8", errors="replace")
    except OSError:
        return ""


def build_prompt(agent: str, previous: digest.Digest, events, day: str) -> str:
    """The fold prompt. One call, cheap model, no conversation."""
    prev_text = digest.render(previous) if previous.agent else "(no previous digest)"
    delta = []
    for e in events:
        when = time.strftime("%H:%M", time.localtime(e["ts"]))
        delta.append(f"{when} [{e['kind']}] {e['payload'] or ''}")
    headings = "\n".join(f"## {h}" for _, h in digest.SECTIONS)
    return f"""Rewrite the work digest below. Output ONLY the digest in markdown, nothing else.

Required format, with these exact section headings, all of them present:

# {day} · {agent}
updated: {time.strftime('%H:%M')} · folded_through: event {events[-1]['id'] if events else previous.folded_through}

{headings}

Rules:
- Every item already under 완료 in the previous digest MUST still appear under 완료.
  Losing a completed item is the one unacceptable error.
- Move items between sections as the new events warrant; do not invent work.
- Keep each item one line, terse and concrete.
- Do not add sections beyond the ones listed.

=== previous digest ===
{prev_text}

=== new events since the last fold ===
{chr(10).join(delta) if delta else '(none)'}
"""


def run_model(prompt: str, account: str, model: str = "haiku") -> tuple[bool, str]:
    """Run the single model call a fold is allowed (KICKOFF rule 4)."""
    claude = shutil.which("claude")
    if not claude:
        return False, "claude not on PATH"
    if not accounts.logged_in(account):
        return False, f"account '{account}' is not logged in"
    env = accounts.env_for(account)
    try:
        r = subprocess.run(
            [claude, "--model", model, "-p", prompt],
            capture_output=True, text=True, timeout=FOLD_TIMEOUT_S, env=env,
        )
    except subprocess.TimeoutExpired:
        return False, f"fold timed out after {FOLD_TIMEOUT_S}s"
    except OSError as e:
        return False, f"could not run claude: {e}"
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or "claude exited non-zero").strip()[:500]
    return True, r.stdout.strip()


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines)
    return t.strip()


def fold(agent: str, use_model: bool = True, model: str = "haiku",
         fold_account: str | None = None, repo: Path | None = None) -> tuple[bool, str]:
    """Fold one agent's events into its digest.

    Returns (ok, message). On any failure the previous digest is left intact
    and the cursor does not move, so the same delta is retried next time.
    """
    day = today()
    jpath = journal_path(agent, day)
    previous = digest.parse(jpath.read_text()) if jpath.exists() else digest.Digest()
    if not previous.agent:
        # Fall back to the most recent earlier day, so a fold across midnight
        # still sees history.
        earlier = sorted((paths.agent_dir(agent) / "journal").glob("*.md"))
        if earlier:
            previous = digest.parse(earlier[-1].read_text())

    con = db.connect()
    try:
        cursor_name = f"fold:{agent}"
        cursor = db.get_cursor(con, cursor_name)
        events = db.events_after(con, cursor, agent=agent,
                                 exclude_kinds=db.FOLD_META_KINDS)
        facts = deterministic_facts(agent, events, repo=repo)

        if not events:
            # Nothing to fold, but still refresh the deterministic block so a
            # resume never reads stale facts.
            _write_handoff(agent, previous, facts)
            return True, f"{agent}: nothing new to fold (cursor at {cursor})"

        top = events[-1]["id"]
        candidate = ""
        used_model = False
        if use_model:
            acct = fold_account or _cheapest_account()
            if acct:
                ok, out = run_model(build_prompt(agent, previous, events, day), acct, model)
                if ok:
                    candidate, used_model = _strip_fences(out), True
                else:
                    reason = out
            else:
                reason = "no logged-in account available for the fold call"
        else:
            reason = "model disabled"

        if candidate:
            valid, problems = digest.validate(candidate, previous if previous.agent else None)
        else:
            valid, problems = False, [reason]

        if not valid:
            # Degraded fold: deterministic only. The previous digest survives,
            # the cursor does not move, and the delta is retried next time.
            degraded = _degraded(agent, previous, events, day, facts)
            _publish(agent, jpath, degraded, commit_msg=f"fold {agent} {day} (degraded)")
            _write_handoff(agent, digest.parse(degraded), facts)
            db.add_event(con, agent, "fold_failed", {"problems": problems, "cursor": cursor})
            return False, (f"{agent}: fold rejected, kept the previous digest and left the "
                           f"cursor at {cursor}. Reason: {'; '.join(problems)[:300]}")

        # Valid. Publish, then advance the cursor in the same transaction that
        # records the fold, so a crash cannot leave them disagreeing.
        merged = digest.parse(candidate)
        merged.facts = facts
        merged.folded_through = top
        text = digest.render(merged)
        _publish(agent, jpath, text, commit_msg=f"fold {agent} {day}")
        _write_handoff(agent, merged, facts)

        con.execute("BEGIN IMMEDIATE")
        try:
            db.set_cursor(con, cursor_name, top)
            db.add_event(con, agent, "fold", {"through": top, "events": len(events),
                                              "model": model if used_model else None})
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        return True, f"{agent}: folded {len(events)} events through {top}"
    finally:
        con.close()


def _cheapest_account() -> str | None:
    from . import status

    picked, _ = status.pick_account()
    if picked:
        return picked
    # Fall back to any logged-in account: a fold is small and it is better to
    # record the day than to skip it.
    for a in paths.list_accounts():
        if accounts.logged_in(a):
            return a
    return None


def _degraded(agent: str, previous: digest.Digest, events, day: str, facts: str) -> str:
    """A digest built with no model at all: previous content plus the facts."""
    d = digest.Digest(date=day, agent=agent or previous.agent,
                      updated=f"{time.strftime('%H:%M')} (degraded, no model)",
                      folded_through=previous.folded_through,
                      sections={k: list(previous.items(k)) for k in digest.SECTION_KEYS})
    d.facts = facts
    d.late = [f"fold could not run; {len(events)} event(s) still pending"]
    return digest.render(d)


def _publish(agent: str, path: Path, text: str, commit_msg: str) -> None:
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(text)
    tmp.replace(path)
    home = paths.home()
    if (home / ".git").exists():
        # Section 3 keeps the home in git so digest generations and rollback
        # come for free.
        subprocess.run(["git", "add", str(path.relative_to(home))], cwd=home,
                       capture_output=True, check=False)
        subprocess.run(["git", "commit", "-q", "-m", commit_msg], cwd=home,
                       capture_output=True, check=False)


def _write_handoff(agent: str, d: digest.Digest, facts: str) -> Path:
    """handoff/latest.md - machine-extracted, injected on resume.

    The deterministic block is always included, so a resume after a failed fold
    still has the branch, the recent commits and the recent events.
    """
    p = handoff_path(agent)
    parts = [f"# handoff · {agent}", f"generated: {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    for key in ("in_progress", "blocked", "next"):
        items = d.items(key)
        parts.append(f"## {digest.HEADINGS[key]}")
        parts.extend(f"- {i}" for i in items)
        if not items:
            parts.append("- (none)")
        parts.append("")
    parts.append(f"## {digest.FACTS_HEADING}")
    parts.append(facts)
    parts.append("")
    tmp = p.with_suffix(".md.tmp")
    tmp.write_text("\n".join(parts))
    tmp.replace(p)
    return p


def rollover(agent: str) -> tuple[bool, str]:
    """Midnight rollover, no model (section 5).

    Without this, yesterday's 'in progress' stays in progress forever.
    """
    day = today()
    jdir = paths.agent_dir(agent) / "journal"
    jdir.mkdir(parents=True, exist_ok=True)
    target = journal_path(agent, day)
    if target.exists():
        return True, f"{agent}: {day} digest already exists"
    earlier = sorted(p for p in jdir.glob("*.md") if p.name != f"{day}.md")
    if not earlier:
        return True, f"{agent}: nothing to roll over"
    previous = digest.parse(earlier[-1].read_text())
    carried = digest.carry_forward(previous, day)
    carried.facts = previous.facts
    _publish(agent, target, digest.render(carried), commit_msg=f"rollover {agent} {day}")
    return True, (f"{agent}: rolled {earlier[-1].name} -> {day}.md "
                  f"(carried {len(carried.items('in_progress'))} in progress, "
                  f"{len(carried.items('blocked'))} blocked)")
