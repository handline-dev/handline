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
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from ..team import accounts, agents
from .. import db, vendors
from . import digest, events as evmod
from ..infra import atomic, gitrun, paths, textsafe, timefmt
from ..usage import status

FOLD_TIMEOUT_S = 180


def journal_path(agent: str, day: str) -> Path:
    d = paths.agent_dir(agent) / "journal"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{day}.md"


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _git(args: list[str], cwd: Path, strip: bool = True) -> str:
    """Run git and return stdout.

    strip=False matters for `status --porcelain`: its status field is two
    columns wide and the first can be a space, so stripping the whole output
    eats a character off the first filename.
    """
    rc, out, _ = gitrun.run(cwd, *args)
    if rc != 0:
        return ""
    return out.strip() if strip else out.rstrip("\n")


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
        # The last few events that are not turn boundaries. Those are counted
        # above and say nothing one at a time: a hundred identical SubagentStop
        # lines filled a handoff and told the agent nothing about its work.
        told = [e for e in events if e["kind"] not in evmod.TURN_BOUNDARIES][-6:]
        if told:
            lines.append("- last events:")
            for e in told:
                when = timefmt.stamp(e["ts"], timefmt.CLOCK)
                lines.append(f"    {when} {e['kind']} {(e['payload'] or '')[:90]}")
    else:
        lines.append("- events folded: 0 (nothing new since the last fold)")
    lines.append(f"- generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    return "\n".join(lines)


# A fold's own event delta cannot tell it which of an agent's outstanding
# asks an "answered" event closed - that event carries only a count (ocp/daemon/
# deliver_answers), never the ids, because the row itself is the record and a
# second copy in the event log would just be one more place to drift from it.
# So a request an agent is still asking about, in the 진행중/막힌 것 prose the
# model wrote last time, can sit there for folds after the store already has
# it as done: nothing in the delta ever named it closed. This checks the
# store directly for every id already on the page, rather than asking the
# model to infer closure from a delta that structurally cannot say it.
#
# reconcile_resolved's errors are one-way: build_prompt (below) forbids the
# very next fold from ever moving a line back out of 완료, so a wrong close
# here is permanent, in the document an agent is told to read when it has no
# handoff (ocp/daemon/'s no-handoff branch) and that the next fold itself reads
# back as `previous`. That rules out guessing an id out of prose - \d+ moved
# "step 2 of 7", "3 conflicts left" and "7d reset tracking needs founder
# call" to 완료 once request ids 1-8 happened to be resolved, none of them
# actually about those requests (auditor's #499/#1573). Only an explicit
# reference is close enough to certain: #N, or "request N" the way an agent
# writing prose about its own asks would say it. build_prompt's own rules
# tell the model to write an ask as #N, so this matches something the prompt
# guarantees rather than something it merely hopes for.
_ID_RE = re.compile(r"#(\d+)|\brequest\s+(\d+)\b", re.IGNORECASE)
_RECONCILE_SECTIONS = ("in_progress", "blocked")


def _is_resolved(con, rid: int) -> bool:
    # Only "done" - not db.NEVER_REACHED_TARGET (denied/withdrawn/replaced)
    # and not "rejected": every one of those means the work this line
    # describes never happened, and reconcile_resolved's job is to close a
    # line once the store can PROVE it is done, not once the request is off
    # the queue for some other reason (auditor's #499 should-fix, raised to
    # blocking by #1573's ruling - the same one-way-error reasoning above).
    r = db.get_request(con, rid)
    return r is not None and r["state"] == "landed"


def reconcile_resolved(con, d: digest.Digest) -> digest.Digest:
    """Move a 진행중/막힌 것 line to 완료 once every id it names is resolved.

    A line with no ids, or with an id the store does not recognize or has not
    resolved, stays exactly where the model put it - this only closes what the
    store can prove is done, never rewrites a line on its own judgment.
    """
    for key in _RECONCILE_SECTIONS:
        items = d.sections.get(key, [])
        if not items:
            continue
        kept, resolved = [], []
        for item in items:
            # Two alternatives, two groups: whichever one matched is the id,
            # the other is "".
            ids = [int(a or b) for a, b in _ID_RE.findall(item)]
            (resolved if ids and all(_is_resolved(con, i) for i in ids) else kept).append(item)
        if resolved:
            d.sections[key] = kept
            d.sections["done"] = d.sections.get("done", []) + resolved
    return d


def build_prompt(agent: str, previous: digest.Digest, events, day: str) -> str:
    """The fold prompt. One call, cheap model, no conversation."""
    prev_text = digest.render(previous) if previous.agent else "(no previous digest)"
    delta = []
    for e in events:
        when = timefmt.stamp(e["ts"], timefmt.CLOCK)
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
- When a 진행중 or 막힌 것 item is about a request you asked, write its id as
  #N (e.g. "waiting on #7") ONLY when that request is what the item is
  waiting on - reconcile_resolved closes the item the moment that request
  is done, so #N there means "done" ends this. A request being resolved is
  often when work on it starts, not when it finishes (e.g. a review coming
  back) - name that request another way instead ("auditor's review", "the
  C1 finding"), never as #N or "request N", or the item will close the
  moment the review lands, not when the work it starts is actually done.
- Keep each item one line, terse and concrete.
- Do not add sections beyond the ones listed.

=== previous digest ===
{prev_text}

=== new events since the last fold ===
{chr(10).join(delta) if delta else '(none)'}
"""


def run_model(prompt: str, account: str, model: str | None = None) -> tuple[bool, str]:
    """Run the single model call a fold is allowed (AGENTS.md rule 4).

    `model` is a human's own choice (`--model`), or None to let the driver
    pick its own cheap default - never a literal fallen back to here, which
    would be one vendor's model alias handed to whichever vendor answers.
    """
    drv = vendors.for_account(account)
    exe = drv.binary()
    if not exe:
        return False, f"{drv.name} not on PATH"
    argv = drv.headless_argv(exe, model, prompt)
    if argv is None:
        return False, f"{drv.name} has no one-shot run; fold is refused"
    if not accounts.logged_in(account):
        return False, f"account '{account}' is not logged in"
    env = accounts.env_for(account)
    try:
        r = subprocess.run(
            argv, capture_output=True, text=True, timeout=FOLD_TIMEOUT_S, env=env,
        )
    except subprocess.TimeoutExpired:
        return False, f"fold timed out after {FOLD_TIMEOUT_S}s"
    except OSError as e:
        return False, f"could not run {drv.name}: {e}"
    if r.returncode != 0:
        # The vendor's own stderr, not anything of ours - clean it here
        # rather than trust every future reader of a fold refusal to remember to.
        return False, textsafe.oneline((r.stderr or r.stdout
                                        or f"{drv.name} exited non-zero").strip())[:500]
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


def fold(agent: str, use_model: bool = True, model: str | None = None,
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
            # No event of this agent's own to fold, but the store can still
            # have resolved an ask with no event to show it - a resolve event
            # can fall before this fold's own cursor, so the model never sees
            # it in the delta and the line it wrote sits in 진행중/막힌 것
            # forever. Reconcile against the store here too, and republish
            # only if that moved a line. reconcile_resolved mutates its
            # argument in place, so the comparison needs the text taken
            # before it runs.
            before = digest.render(previous)
            reconciled = reconcile_resolved(con, previous)
            after = digest.render(reconciled)
            if after != before:
                _publish(agent, jpath, after, commit_msg=f"fold {agent} {day} (reconcile)")
            return True, f"{agent}: nothing new to fold (cursor at {cursor})"

        top = events[-1]["id"]
        candidate = ""
        used_model = False
        acct = None
        spent_model = model
        if use_model:
            acct = fold_account or _fold_account(agent)
            if acct:
                # What actually runs, for the spend record below - `model`
                # itself once the driver has looked at it, since a fold that
                # asked for none still spends whatever the driver defaults to.
                spent_model = vendors.for_account(acct).headless_model(model)
                ok, out = run_model(build_prompt(agent, previous, events, day), acct, spent_model)
                if ok:
                    candidate, used_model = _strip_fences(out), True
                else:
                    reason = out
            else:
                reason = f"no account can pay for the fold call: {_account_reasons()}"
        else:
            reason = "model disabled"

        if candidate:
            # Guard on whether there is previous *content*. Keying this on the
            # header parsing meant a hand-edited or odd header skipped the one
            # check that stops a fold from dropping completed work.
            had_content = any(previous.items(k) for k in digest.SECTION_KEYS)
            valid, problems = digest.validate(candidate, previous if had_content else None,
                                              header_is_stamped=True)
        else:
            valid, problems = False, [reason]

        if not valid:
            # Degraded fold: deterministic only. The previous digest survives,
            # the cursor does not move, and the delta is retried next time.
            degraded_d = reconcile_resolved(con, digest.parse(_degraded(agent, previous, events, day, facts)))
            degraded = digest.render(degraded_d)
            _publish(agent, jpath, degraded, commit_msg=f"fold {agent} {day} (degraded)")
            # A rejected fold spent exactly what an accepted one spends, and
            # these events are the only record that the account was charged -
            # so leaving the account off here made the spend reconstructable
            # for the folds that worked and blank for the ones that went wrong.
            db.add_event(con, agent, "fold_failed",
                         {"problems": problems, "cursor": cursor,
                          "model": spent_model if used_model else None,
                          "account": acct})
            return False, (f"{agent}: fold rejected, kept the previous digest and left the "
                           f"cursor at {cursor}. Reason: {'; '.join(problems)[:300]}")

        # Valid. Publish first, then advance the cursor. A crash in between
        # leaves a published digest with an un-advanced cursor, which simply
        # re-folds the same delta next time - the safe direction.
        # The header is our bookkeeping, not the model's narration, so it is
        # stamped rather than accepted. Asked for the right values, a fold came
        # back saying it was updated at 20:12 - the time of the last event it
        # had just read, fourteen hours earlier.
        merged = reconcile_resolved(con, digest.parse(candidate))
        merged.facts = facts
        merged.date = day
        merged.agent = agent
        merged.updated = time.strftime("%H:%M")
        merged.folded_through = top
        text = digest.render(merged)
        _publish(agent, jpath, text, commit_msg=f"fold {agent} {day}")

        con.execute("BEGIN IMMEDIATE")
        try:
            db.set_cursor(con, cursor_name, top)
            # A fold is the one spender no status line observes, so this event
            # is the only record that the account was charged at all.
            db.add_event(con, agent, "fold", {"through": top, "events": len(events),
                                              "model": spent_model if used_model else None,
                                              "account": acct})
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        return True, f"{agent}: folded {len(events)} events through {top}"
    finally:
        con.close()


def _fold_account(agent: str) -> str | None:
    """The account that pays for this agent's fold call: one of its own
    vendor's, if that vendor can run one-shot at all and one is usable, else
    one of claude's - the vendor every fold reached before vendors existed."""
    own = vendors.agent_vendor(agent)
    tries = [own] if vendors.get(own).can_headless() else []
    if agents.DEFAULT_VENDOR not in tries:
        tries.append(agents.DEFAULT_VENDOR)
    for vendor in tries:
        acct = _cheapest_account(vendor)
        if acct:
            return acct
    return None


def _cheapest_account(vendor: str = agents.DEFAULT_VENDOR) -> str | None:
    # Only an account with a genuinely current reading, never one pick_account
    # admitted on a stale floor (`status.idle_floor`, `rolled_over`): those are
    # safe for a launch, which the daemon asks the endpoint about on its next
    # tick and self-corrects, but a fold is the one spend nothing observes -
    # sending it to an account we already cannot read only deepens the
    # blindness. Same rule the fallback below applies.
    # A fold runs headless, under whichever vendor can (`_fold_account`
    # picks it) - one account of that vendor, and one ocp can read.
    ours = [a for a in paths.list_accounts() if status.not_for(a, vendor) is None]
    readable = [a for a in ours if accounts.logged_in(a) and not status.read(a).stale]
    picked, _ = status.pick_account(readable)
    if picked:
        return picked
    # Fall back to any logged-in account we can still read. A fold is small and
    # recording the day beats skipping it, but two accounts are off limits: one
    # already at the wall, and one whose usage we cannot read at all.
    #
    # The second matters more than it looks. A fold runs `claude -p` from the
    # daemon's own directory: it fires no hook of ours and its transcript is
    # not under any agent's worktree, so it is the one spender nothing sees -
    # not the usage poll either, which asks an account about a turn only when
    # an event says one happened. Letting it fall back to the accounts we cannot read would send
    # the unobservable spend precisely where the usage is already unobserved.
    # Reading the raw figure did exactly that: None passed the test.
    # Roomiest first: on this path nothing was assignable, so the difference
    # between accounts is all we have, and putting the one unobservable spend
    # on the tightest readable account while a roomier one sits beside it is
    # the avoidable version of this call's risk.
    usable = []
    for a in ours:
        if not accounts.logged_in(a):
            continue
        pct = status.read(a).short_trusted
        if pct is None or pct >= status.SHORT_BLOCK:
            continue
        usable.append((pct, a))
    usable.sort()
    return usable[0][1] if usable else None


def _account_reasons() -> str:
    """Why each account was passed over, for the line the human reads.

    "No logged-in account" was what this used to say, and it sent a reader to
    the one thing that was not wrong: an account is refused here for being at
    the wall, or for a usage figure nothing can read, far more often than for
    credentials.
    """
    out = []
    for a in paths.list_accounts():
        if not accounts.logged_in(a):
            out.append(f"{a} not logged in")
            continue
        st = status.read(a)
        pct = st.short_trusted
        if pct is None:
            out.append(f"{a} usage unreadable ({st.assignable()[1]})")
        elif pct >= status.SHORT_BLOCK:
            out.append(f"{a} at {pct}%")
        else:
            out.append(f"{a} at {pct}% (usable)")
    return ", ".join(out) or "no accounts registered"


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
    atomic.write_text(path, text)
    home = paths.home()
    if (home / ".git").exists():
        # Section 3 keeps the home in git so digest generations and rollback
        # come for free.
        subprocess.run(["git", "add", str(path.relative_to(home))], cwd=home,
                       capture_output=True, check=False)
        subprocess.run(["git", "commit", "-q", "-m", commit_msg], cwd=home,
                       capture_output=True, check=False)


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
