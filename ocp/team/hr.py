"""Changing the roster, which is the one thing an agent may not simply do.

A team that cannot grow has one way to take on more work: each agent quietly
making helpers of its own, which die with the session that made them and appear
on no screen. So growing is a first-class operation - and it is the operation
with the least business being automatic, because an agent that can hire can
spend an account into the ground without anyone deciding to.

The shape is therefore: any agent may *propose*, only the founder may approve,
and the proposal is applied by the same command that raised it, from the file
it wrote. Three properties fall out of that:

  - It is a file. `hr/<id>.yaml` holds what was asked for, who asked, the
    decision it was raised as, and what happened to it. A crash between the
    approval and the change leaves the proposal there to be applied again.
  - The approval is read, not interpreted. The verdict's first word decides,
    out of a vocabulary the decision itself prints. A model reading "maybe
    next week" as approval is the failure this exists to prevent.
  - Applying is the same two writes a human would make: `agent register`, and
    the agent's name in team.yaml. Nothing bypasses the routing table; the
    table is the thing being edited.

Retiring is the same in reverse, and archives rather than deletes: an agent's
journal and handoffs are the record of work that was actually done.
"""
from __future__ import annotations

import json
import re
import shlex
import time
import uuid
from pathlib import Path

from . import agent_settings, agents, ask as askmod, decisions, team
from .. import db, vendors
from ..infra import atomic, paths, sealed, textsafe
from ..infra import timefmt
from ..infra.yamlcompat import ConfigError, dump, is_plain_text, load

# What the founder types to decide. Printed in the decision, so the words are
# in front of whoever answers rather than in a document they have not read.
# 승인 and 반려 are approve and reject: the founder answers in Korean.
APPROVE = ("approve", "approved", "yes", "ok", "go", "승인")
REJECT = ("reject", "rejected", "no", "deny", "denied", "반려")

KINDS = ("add", "retire", "rename", "link", "unlink", "team", "settings")
EDGE_KINDS = ("link", "unlink")


def dir_() -> Path:
    d = paths.home() / "hr"
    d.mkdir(parents=True, exist_ok=True)
    return d


def path_of(pid: str) -> Path:
    return dir_() / f"{pid}.yaml"


def load_proposal(pid: str) -> dict | None:
    p = path_of(pid)
    if not p.exists():
        return None
    data = load(p.read_text())
    return data if isinstance(data, dict) else None


def save(prop: dict) -> Path:
    p = path_of(prop["id"])
    atomic.write_text(p, dump(prop))
    return p


def is_proposal(data) -> bool:
    """Whether a parsed file is a proposal at all: what every reader of the
    listing takes for granted, so a map that is something else is not one."""
    return (isinstance(data, dict) and "id" in data and data.get("kind") in KINDS
            and bool(data.get("agent")))


def listing(unreadable: list[str] | None = None) -> list[dict]:
    """Every proposal that can be read. A file that cannot be - it does not
    parse, it is not text, or it parses to something that is not a proposal -
    is left out and, when `unreadable` is given, named there with why: the
    bridge's waiting list and `ocp hr list` both read this, and one bad file
    must not take either down."""
    out = []
    for f in sorted(dir_().glob("*.yaml")):
        try:
            data = load(f.read_text())
        except (ConfigError, UnicodeDecodeError, OSError) as e:
            if unreadable is not None:
                unreadable.append(f"{f.name}: {e}")
            continue
        if is_proposal(data):
            out.append(data)
        elif unreadable is not None:
            unreadable.append(f"{f.name}: not a proposal")
    return out


def _draft(kind: str, name: str, requester: str, why: str,
           team_name: str | None, repo: str | None, model: str | None,
           to: str | None, effort: str | None = None,
           vendor: str | None = None, response: str | None = None,
           relation: str | None = None, members: list[str] | None = None,
           repos: dict[str, str] | None = None,
           sets: dict[str, str] | None = None) -> tuple[dict, str]:
    """Check what was asked for and write it down. Returns (proposal, refusal).
    A `team` is `name` (its manager and the team's name) with `members`, each a
    suffix (`sea` is `name.sea`) or a full name, and `repos` by member."""
    if kind not in KINDS:
        return {}, f"kind must be one of {', '.join(KINDS)}"
    if not name or "/" in name or name.startswith("."):
        return {}, f"bad agent name: {name!r}"
    if not is_plain_text(name):
        return {}, f"bad agent name: {name!r}. {agents.NOT_TEXT}"
    if not (why or "").strip():
        return {}, "say why: the founder is being asked to decide, not to rubber-stamp"

    registered = paths.list_agents()
    full: list[str] = []
    if kind == "team":
        full, err = _team_names(name, members or [], repos or {}, registered)
        if err:
            return {}, err
    if kind == "add" and name in registered:
        return {}, f"{name} is already an agent"
    if kind in ("retire", "rename", "settings") + EDGE_KINDS and name not in registered:
        return {}, f"{name} is not an agent"
    if kind in EDGE_KINDS:
        err = _edge_refusal(kind, name, to, response, relation, registered)
        if err:
            return {}, err
    if kind == "rename":
        if not to or "/" in to or to.startswith("."):
            return {}, "a rename needs a new name (--to)"
        if not is_plain_text(to):
            return {}, f"bad agent name: {to!r}. {agents.NOT_TEXT}"
        if to in registered:
            return {}, f"{to} is already an agent"
    if team_name and not is_plain_text(team_name):
        return {}, f"bad team name: {team_name!r}. {agents.NOT_TEXT}"
    if effort and effort not in agents.VALID_EFFORT:
        return {}, f"effort must be one of {', '.join(agents.VALID_EFFORT)}"
    if vendor:
        try:
            vendors.get(vendor)
        except vendors.UnknownVendor as e:
            return {}, str(e)

    if kind == "settings":
        if not sets:
            return {}, "name at least one setting to change"
        _, problem = agent_settings.changes_for(name, sets)
        if problem:
            return {}, problem
    if kind == "add":
        repo = repo or _team_repo(team_name)
        if not repo:
            return {}, (f"{name} needs a repo: --repo <path> (or --team of a manager that has "
                        f"one). Orca makes its worktree there; without one it never starts.")
    if kind == "team":
        if not repo:
            return {}, (f"team {name} needs --repo <path> for its manager; a member without "
                        f"its own --member SUFFIX=REPO works there too. Orca makes each "
                        f"worktree in it.")
        repos = {n: (repos or {}).get(n) or repo for n in full}     # written into every agent.yaml

    prop = {
        "id": uuid.uuid4().hex[:8],
        "kind": kind,
        "agent": name,
        "requested_by": requester,
        "why": why.strip(),
        "created": int(time.time()),
        "state": "proposed",
    }
    for key, value in (("team", team_name), ("repo", repo), ("model", model),
                       ("effort", effort), ("to", to), ("vendor", vendor),
                       ("response", response), ("relation", relation)):
        if value:
            prop[key] = value
    for key, value in (sets or {}).items():
        prop[f"set.{key}"] = str(value)
    if full:
        prop["members"] = ",".join(full)
        for n in full:
            if (repos or {}).get(n):
                prop[f"repo.{n}"] = repos[n]
    return prop, ""


def _team_repo(team_name: str | None) -> str | None:
    """The repo the team's manager works in, which a new member may default to; it is
    written into the member's own agent.yaml, never read through the team later."""
    if not team_name:
        return None
    try:
        return agents.read(team_name).get("repo") or None
    except Exception:  # noqa: BLE001 - no such manager, or an unreadable file: no default
        return None


def _team_names(name: str, members: list[str], repos: dict[str, str],
                registered: list[str]) -> tuple[list[str], str]:
    """(members' full names, refusal) for a team: the manager is `name`, a member
    given as a suffix is `<name>.<suffix>`. Nothing may exist yet, in the roster or
    in team.yaml, so the one approval adds exactly what it says."""
    if not members:
        return [], "a team needs at least one member (--member); one agent is `hr add`"
    full = [m if m.startswith(f"{name}.") else f"{name}.{m}" for m in members]
    for n in [name, *full]:
        if not is_plain_text(n) or "/" in n or "," in n or " " in n:
            return [], f"bad agent name: {n!r}. {agents.NOT_TEXT}"
    if len(set(full)) != len(full) or name in full:
        return [], "a member is named twice"
    taken = [n for n in [name, *full] if n in registered]
    if taken:
        return [], f"{', '.join(taken)} already an agent"
    stray = [m for m in repos if m not in full and m != name]
    if stray:
        return [], f"a repo was given for {', '.join(stray)}, who is not in the team"
    try:
        t = team.Team.load()
    except Exception as e:  # noqa: BLE001
        return [], f"team.yaml cannot be read ({e})"
    if name in t.teams:
        return [], f"team {name} is already in team.yaml"
    listed = [n for n in [name, *full] if n in t.agents]
    if listed:
        return [], f"{', '.join(listed)} already in team.yaml agents"
    return full, ""


def propose(kind: str, name: str, requester: str, why: str,
            team_name: str | None = None, repo: str | None = None,
            model: str | None = None, to: str | None = None,
            effort: str | None = None, vendor: str | None = None,
            response: str | None = None,
            relation: str | None = None, members: list[str] | None = None,
            repos: dict[str, str] | None = None,
            sets: dict[str, str] | None = None) -> tuple[bool, str]:
    """Write the proposal down and raise it to the founder. Nothing changes yet."""
    prop, err = _draft(kind, name, requester, why, team_name, repo, model,
                       to, effort, vendor, response, relation, members, repos, sets)
    if err:
        return False, err
    # Written down before it is raised: a proposal that cannot be recorded must
    # not leave a request in front of the founder that names one.
    try:
        dump(prop)
    except ConfigError as e:
        return False, f"could not write the proposal down, so nothing was raised: {e}"
    from .. import actions      # a decision is a task of the founder's
    ok, said, rid = actions.open_decision(requester, _decision_text(prop))
    if not ok:
        return False, f"could not raise it: {said}"
    prop["request"] = rid
    con = db.connect()
    try:
        prop["task"] = task_of_card(con, rid)       # T785: the proposal is keyed by its decision task
    finally:
        con.close()
    save(prop)
    return True, (f"proposal {prop['id']} raised as #{rid}. Nothing changes until the "
                  f"founder answers it; an approve is applied then, and you are told how it went.")


def enact(kind: str, name: str, why: str,
          team_name: str | None = None, repo: str | None = None,
          model: str | None = None, to: str | None = None,
          effort: str | None = None, con=None,
          vendor: str | None = None, response: str | None = None,
          relation: str | None = None, members: list[str] | None = None,
          repos: dict[str, str] | None = None,
          sets: dict[str, str] | None = None) -> tuple[bool, str]:
    """The founder changing the roster directly. No proposal to approve.

    The gate exists to stop an *agent* hiring on its own, and the founder is the
    thing on the other side of it: asking them to approve a request they just
    typed is ceremony, and it is also the only way to make the first agent -
    a proposal needs an agent to raise it, and an empty roster has none.

    The file is written before anything changes, as a proposal's is, so a
    change that fails halfway still leaves a record of what was attempted.
    """
    prop, err = _draft(kind, name, "human", why, team_name, repo, model,
                       to, effort, vendor, response, relation, members, repos, sets)
    if err:
        return False, err
    prop["state"] = "enacting"
    save(prop)
    own = con is None
    con = con or db.connect()
    try:
        doer = _DOERS[kind]
        try:
            ok, said = doer(prop, con)
        except Exception as e:  # noqa: BLE001 - recorded, then raised on
            prop["state"] = "failed"
            prop["error"] = f"{type(e).__name__}: {e}"
            save(prop)
            raise
        if not ok:
            prop["state"] = "failed"
            prop["error"] = said
            save(prop)
            return False, said
        prop["state"] = "applied"
        prop["applied"] = int(time.time())
        prop["verdict"] = "enacted by the founder"
        save(prop)
        return True, said
    finally:
        if own:
            con.close()


def _decision_text(prop: dict, caveat: bool = True) -> str:
    """What the founder reads. It says what will happen and how to decide.
    `caveat=False` leaves out the unlink caveat, which reads the live team.yaml
    and so differs from one moment to the next (see `answered`)."""
    lines = [f"[roster] {prop['kind']} {prop['agent']}"
             + (f" -> {prop['to']}" if prop["kind"] in EDGE_KINDS else "")
             + f" - asked by {prop['requested_by']}",
             "", prop["why"], ""]
    if prop["kind"] == "link":
        lines += [f"{prop['agent']} would be allowed to ask {prop['to']} "
                  f"(response {prop.get('response') or 'async'}"
                  + (f", relation {prop['relation']}" if prop.get("relation") else "")
                  + "). One edge is added to team.yaml; nothing else in the routing changes."]
    elif prop["kind"] == "unlink":
        try:
            note = _unlink_caveat(team.Team.load(), prop["agent"], prop["to"]) if caveat else ""
        except Exception:  # noqa: BLE001 - the refusal at apply time says why
            note = ""
        lines += [f"The edge {prop['agent']} -> {prop['to']} would be removed from team.yaml. "
                  "Requests already queued on it stay queued." + note]
    elif prop["kind"] == "add":
        lines += ["It would be registered"
                  + (f" in team {prop['team']}" if prop.get("team") else "")
                  + (f", working in {prop['repo']}" if prop.get("repo") else "")
                  + (f", model {prop['model']}" if prop.get("model") else "")
                  + (f", effort {prop['effort']}" if prop.get("effort") else "")
                  + (f", vendor {prop['vendor']}"
                     if prop.get("vendor") and prop["vendor"] != agents.DEFAULT_VENDOR else "")
                  + ".",
                  "team.yaml gains its name; nothing else in the routing changes."]
    elif prop["kind"] == "team":
        lines += [f"Team {prop['agent']} would be created: manager {prop['agent']}"
                  + (f" (working in {prop['repo']})" if prop.get("repo") else "") + ", and"]
        lines += [f"  {n}" + (f" (working in {prop[f'repo.{n}']})" if prop.get(f"repo.{n}") else "")
                  for n in _members_of(prop)]
        lines += ["all registered"
                  + (f", model {prop['model']}" if prop.get("model") else "")
                  + (f", effort {prop['effort']}" if prop.get("effort") else "")
                  + (f", vendor {prop['vendor']}"
                     if prop.get("vendor") and prop["vendor"] != agents.DEFAULT_VENDOR else "")
                  + ". team.yaml gains the team, the agents and one edge "
                  f"({prop['agent']} -> {prop['agent']}.*, supervisor); the default links "
                  "then apply:"]
        lines += [f"  - {d}" for d in team.DEFAULT_LINKS]
    elif prop["kind"] == "settings":
        lines += [f"{prop['agent']}'s settings would become:"]
        lines += [f"  {k}: {v or '(none)'}" for k, v in _sets_of(prop).items()]
        lines += ["Written as the founder's own change, from its next launch; a running "
                  "session is not restarted."]
    elif prop["kind"] == "rename":
        lines += [f"{prop['agent']} would become {prop.get('to')}"
                  + (f" in team {prop['team']}" if prop.get("team") else "")
                  + f": its folder, its "
                  f"journal and handoffs, its place in team.yaml and every request it "
                  f"ever sent or received move with the name.",
                  "It has to be closed first - an identity cannot change under a "
                  "running session."]
    else:
        lines += [f"{prop['agent']} would be taken out of team.yaml and its folder "
                  f"moved to agents/archive/. Its journal and handoffs are kept.",
                  "Anything still queued for it stays queued and undeliverable."]
    lines += ["", f"Answer `approve` or `reject` (proposal {prop['id']})."]
    return "\n".join(lines)


_SUBJECT_FIELDS = ("kind", "agent", "to", "team", "repo", "model", "effort", "vendor",
                   "response", "relation", "requested_by", "why", "members")


def _sets_of(prop: dict) -> dict[str, str]:
    return {k[4:]: str(v) for k, v in prop.items() if k.startswith("set.")}


def _members_of(prop: dict) -> list[str]:
    return [m for m in str(prop.get("members") or "").split(",") if m]


def subject_of(prop: dict) -> str:
    """What the founder is deciding on: every field of the draft that changes
    what applying it does, as a digest."""
    return decisions.digest({**{k: prop.get(k) for k in _SUBJECT_FIELDS},
                             **{k: v for k, v in prop.items() if k.startswith(("repo.", "set."))}})


def answered(prop: dict, row) -> tuple[str | None, str | None]:
    """(subject, refusal) for the answer given on `row`: what was decided on,
    or why this draft is not what the founder was asked. The decision the
    founder read must be the one this draft renders to - every field of the
    change, its reason and its asker - so a draft edited before the answer
    is refused rather than approved unseen. An unlink's caveat is the one
    part that is read live, and is left out of the comparison."""
    body = row["body"] or ""
    want = _decision_text(prop, caveat=False)
    marker = "Requests already queued on it stay queued."
    if prop.get("kind") == "unlink" and marker in want:
        head, tail = want.split(marker, 1)
        ok = body.startswith(head + marker) and body.endswith(tail)
    else:
        ok = body == want
    if not ok:
        return None, (f"the draft is not what #{row['id']} asked the founder to approve "
                      f"- raise it again")
    return subject_of(prop), None


def task_of_card(con, rid: int) -> int | None:
    """The decision task whose card is request `rid`, in whatever state it is now."""
    for t in db.tasks_for_request(con, rid):
        first = db.first_linked_request(con, t["id"])
        if t["expects"] == "decision" and first is not None and int(first["id"]) == int(rid):
            return int(t["id"])
    return None


def task_of(con, prop: dict) -> int | None:
    """The decision task a proposal waits on (T785). A proposal raised before it was keyed by task
    names only its card request: the task is found from that."""
    return int(prop.get("task") or 0) or task_of_card(con, int(prop.get("request") or 0))


def answer_of(con, prop: dict) -> tuple[str, int | None, str | None]:
    """(state, task id, verdict) of the founder's answer to a proposal, read off its decision task:
    "none" (no task to read), "open" (not answered), "discarded" (the card was withdrawn, handed
    back, thrown away or marked done by someone but the founder: nothing was decided) or
    "answered" with the verdict he gave."""
    tid = task_of(con, prop)
    task = db.get_task(con, tid) if tid else None
    if task is None:
        return "none", None, None
    if task["discarded_by"]:
        return "discarded", tid, None
    if task["done_evidence"]:
        # Only the founder's own close is his answer: an agent that was handed the task and marked it
        # done wrote evidence, not a decision, so it reads like a hand-back.
        last = con.execute(
            f"SELECT kind, agent FROM events WHERE {db.EVENT_TASK} = ? AND kind IN "
            "('settle_decision_task', 'mark_task_done', 'reopen_task') ORDER BY id DESC LIMIT 1",
            (tid,)).fetchone()
        if task["owner"] == askmod.HUMAN and last is not None and (last["kind"] == "settle_decision_task"
                                 or (last["kind"] == "mark_task_done" and last["agent"] == askmod.HUMAN)):
            return "answered", tid, task["done_evidence"]
        return "discarded", tid, None
    return "open", tid, None


def card_of(con, tid: int):
    """The request the founder was shown: the decision's own first request."""
    return db.first_linked_request(con, tid)


def decision(verdict: str | None) -> str:
    """approved / rejected / unclear, from the first word of the verdict."""
    first = (verdict or "").strip().split()
    if not first:
        return "unclear"
    word = first[0].strip(".,!:;\"'`").lower()
    if word in APPROVE:
        return "approved"
    if word in REJECT:
        return "rejected"
    return "unclear"


def apply(pid: str, con=None) -> tuple[bool, str]:
    """Carry out a proposal the founder approved. Refuses anything else."""
    try:
        prop = load_proposal(pid)
    except (ConfigError, UnicodeDecodeError, OSError) as e:
        return False, f"proposal {pid} cannot be read: {e}"
    if not prop:
        return False, f"no proposal {pid}"
    if not is_proposal(prop):
        return False, f"{pid} is not a proposal"
    if prop.get("state") == "applied":
        return False, f"proposal {pid} was already applied on {timefmt.stamp(prop.get('applied'), timefmt.DATE_TIME, 'an unknown date')}"
    if prop.get("state") == "withdrawn":
        return False, f"proposal {pid} was withdrawn"
    own = con is None
    con = con or db.connect()
    try:
        state, tid, verdict = answer_of(con, prop)
        if state == "none":
            return False, f"proposal {pid} has no decision to check - raise it again"
        if state == "open":
            return False, (f"the founder has not answered T{tid} yet. It is on "
                           f"`ocp bridge`, top panel.")
        if state == "discarded":
            why = (db.get_task(con, tid)["discard_reason"] or "").strip()
            return False, (f"T{tid}, the decision on proposal {pid}, was discarded{f' ({why})' if why else ''} or handed back or closed by someone but the founder - "
                           f"nothing was decided. Raise it again, or `ocp hr withdraw {pid}`.")
        card = card_of(con, tid)
        call = decision(verdict)
        if call in ("approved", "rejected"):
            why_not = decisions.refusal("hr", pid, tid, subject_of(prop), card["id"] if card else None)
            if why_not or decisions.get("hr", pid)["call"] != call:
                return False, why_not or (f"the founder's recorded answer for proposal {pid} "
                                          f"does not match T{tid}'s answer")
        if call == "rejected":
            prop["state"] = "rejected"
            prop["verdict"] = textsafe.oneline(verdict or "")
            save(prop)
            # The founder's own text, printed raw by cmd_hr - sanitised here
            # rather than trusted to save()'s control-character refusal above,
            # which does not cover the C1 range (auditor, #1048).
            return False, (f"proposal {pid} was rejected: "
                           f"{textsafe.oneline((verdict or '').strip())[:120]}")
        if call != "approved":
            return False, (f"T{tid} was answered "
                           f"{textsafe.oneline((verdict or '').strip())[:60]!r}, "
                           f"which is not a decision. Ask the founder to answer "
                           f"`approve` or `reject`, or `ocp hr withdraw {pid}`.")
        doer = _DOERS[prop["kind"]]
        ok, said = doer(prop, con)
        if not ok:
            return False, said
        prop["state"] = "applied"
        prop["applied"] = int(time.time())
        prop["verdict"] = textsafe.oneline(verdict or "")
        save(prop)
        return True, said
    finally:
        if own:
            con.close()


def confirm_answer(pid: str, con=None) -> tuple[bool, str]:
    """The founder records, from their own session, an answer that was given but
    never recorded (a running daemon or bridge on older code did not know the
    proposal, so it closed the decision without applying it), then applies it.

    The recorded answer is what makes an approve believable, and a verdict in the
    db alone is no proof, so only the founder's session may do this, and only for
    a card closed with a decision that still shows the draft the
    founder was asked. An agent's session is refused by the protected store."""
    try:
        prop = load_proposal(pid)
    except (ConfigError, UnicodeDecodeError, OSError) as e:
        return False, f"proposal {pid} cannot be read: {e}"
    if not is_proposal(prop):
        return False, f"no proposal {pid}"
    if prop.get("state") != "proposed":
        return False, f"proposal {pid} is already {prop.get('state')}"
    own = con is None
    con = con or db.connect()
    try:
        state, tid, verdict = answer_of(con, prop)
        if state in ("none", "open", "discarded"):
            return False, f"the founder has not answered T{tid or prop.get('task') or '?'} yet"
        call = decision(verdict)
        if call == "unclear":
            return False, f"T{tid} was answered without approve or reject"
        card = card_of(con, tid)
        subject, why_not = answered(prop, card)
        if why_not:
            return False, why_not
        try:
            decisions.record("hr", pid, tid, call, subject)
        except PermissionError as e:
            return False, str(e)
    finally:
        if own:
            con.close()
    return apply(pid)


class _Unwound(Exception):
    """A withdraw that had to be taken back, with the reason to show."""


def withdraw(pid: str, me: str, con=None) -> tuple[bool, str]:
    """The proposal's own author, or the founder, takes it back.

    The way out of a proposal that will get no decision: a card closed
    without approve or reject leaves `apply` refusing it and nothing else able
    to end it. A still-queued card is closed with it and marked told:
    the proposer is not messaged, whoever withdrew.

    A withdraw and the founder's answer settle to exactly one outcome, decided
    under one write lock. The decision is read there: an approve or reject
    already on it is the founder's answer and stands, so the withdraw refuses
    and `apply` reads that answer; still queued, the withdraw wins and the
    founder's answer then finds the row closed and refuses. Anything else on
    the row (closed with no decision, or no row) is the stranded case this
    verb exists for.
    """
    prop = load_proposal(pid)
    if not prop:
        return False, f"no proposal {pid}"
    if me not in (prop.get("requested_by"), askmod.HUMAN):
        return False, f"proposal {pid} is {prop.get('requested_by')}'s to withdraw, not yours"
    own = con is None
    con = con or db.connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        saved = False
        try:
            # Read again under the lock: the state read above may be from
            # before another withdraw landed.
            prop = load_proposal(pid) or prop
            if prop.get("state") != "proposed":
                con.execute("ROLLBACK")
                return False, f"proposal {pid} is already {prop.get('state')} - nothing to withdraw"
            state, tid, verdict = answer_of(con, prop)
            row = card_of(con, tid) if tid else None
            if state == "answered" and decision(verdict) != "unclear":
                con.execute("ROLLBACK")
                return False, (f"the founder already answered T{tid} "
                               f"({textsafe.oneline((verdict or '').strip())[:60]!r}) - "
                               f"nothing to withdraw; `ocp hr apply {pid}` reads that answer")
            # Marked withdrawn before the row is closed: `_would_strand_a_proposal`
            # reads `is_open_proposal` off this same file, and this call is the
            # one legitimate way to close the row with something other than
            # approve or reject - so it must already read as no-longer-open by
            # the time the close below runs, or the chokepoint refuses its own
            # escape valve. Put back if the transaction does not commit.
            prop["state"] = "withdrawn"
            save(prop)
            saved = True
            if row is not None and row["state"] == "queued" and not db.close_if_queued(
                    con, row["id"], "rejected", f"withdrawn by {me}", announced=True):
                raise _Unwound(f"#{row['id']} could not be closed - nothing was withdrawn")
            con.execute("COMMIT")
        except BaseException as e:
            if con.in_transaction:
                con.execute("ROLLBACK")
            if saved:
                prop["state"] = "proposed"
                save(prop)
            if isinstance(e, _Unwound):
                return False, str(e)
            raise
        return True, f"proposal {pid} withdrawn"
    finally:
        if own:
            con.close()


def waits_on(prop: dict, rid: int, tid: int | None) -> bool:
    """Is request `rid` (task `tid`'s card) the decision this proposal waits on?"""
    if prop.get("task"):
        return tid is not None and int(prop["task"]) == tid
    return int(prop.get("request") or 0) == rid


def open_proposal_of(rid: int, con=None) -> str | None:
    """The id of the proposal still waiting on approve or reject whose decision is request `rid`
    (the card of its decision task)."""
    props = [p for p in listing() if p.get("state") == "proposed"]
    if not props:
        return None
    own = con is None
    con = con or db.connect()
    try:
        tid = task_of_card(con, rid)
    finally:
        if own:
            con.close()
    for p in props:
        if waits_on(p, rid, tid):
            return p["id"]
    return None


def unknown_kind_of(rid: int) -> str | None:
    """The kind of a proposal file waiting on request `rid` that this process does
    not know, else None. `listing` skips such a file, so without this a process
    older than the kind would close the decision and throw the answer away."""
    con = db.connect()
    try:
        tid = task_of_card(con, rid)
    finally:
        con.close()
    for f in dir_().glob("*.yaml"):
        try:
            data = load(f.read_text())
        except (ConfigError, UnicodeDecodeError, OSError):
            continue
        if (isinstance(data, dict) and data.get("id") and data.get("agent")
                and data.get("kind") not in KINDS and data.get("state") == "proposed"
                and waits_on(data, rid, tid)):
            return str(data.get("kind"))
    return None


def is_open_proposal(rid: int, con=None) -> bool:
    """Is request `rid` the decision of a proposal still waiting on approve or reject? Pass the
    caller's `con` when it is inside a write: a second connection would wait on its lock."""
    return open_proposal_of(rid, con) is not None


def register_repo(repo: str | None) -> str:
    """Put an agent's repo in Orca so its worktree can be made there: what to
    tell the founder, "" when there is no repo. The agent is added whatever Orca
    says; a refusal, no Orca, or a folder without `git init` says what to run."""
    from ..session import orca

    return f" {orca.ensure_repo(repo)[1]}." if repo else ""


def _add(prop: dict, con) -> tuple[bool, str]:
    name = prop["agent"]
    # The table first: it is the write that can be refused, and a refusal after
    # the folder exists leaves an agent nothing routes to.
    ok, said = _in_team_yaml(name, prop.get("team"), add=True)
    if not ok:
        return False, said
    agents.register(name, model=prop.get("model"),
                    effort=prop.get("effort"), repo=prop.get("repo"),
                    vendor=prop.get("vendor"),
                    role=f"Added on {timefmt.stamp(prop.get('created'), timefmt.DATE_TIME, 'an unknown date')} at "
                         f"{prop['requested_by']}'s request: {prop['why']}")
    return True, (f"{name} is an agent"
                  + (f", team {prop['team']}" if prop.get("team") else "")
                  + f". {said}" + register_repo(prop.get("repo")))


def _team(prop: dict, con) -> tuple[bool, str]:
    """A whole team in one approval: the manager and its members in team.yaml with
    the one supervisor edge (the rest is `team.DEFAULT_LINKS`), then each one
    registered. team.yaml is written first, as `_add` does, and once."""
    name, members = prop["agent"], _members_of(prop)
    everyone = [name, *members]

    def edit(text: str) -> str:
        for n in everyone:
            text = _add_text(text, n, name)
        return _link_text(text, name, f"{name}.*", "async", "supervisor", pattern=True)

    def verify(tm) -> None:
        for n in members:
            _need(tm.route(name, n).allowed, f"{name} -> {n} is not routable")

    ok, said = _edit_team_yaml(edit, present=name, verify=verify)
    if not ok:
        return False, said
    role = (f"Added on {timefmt.stamp(prop.get('created'), timefmt.DATE_TIME, 'an unknown date')} at "
            f"{prop['requested_by']}'s request, with team {name}: {prop['why']}")
    for n in everyone:
        agents.register(n, model=prop.get("model"), effort=prop.get("effort"),
                        repo=prop.get("repo") if n == name else prop.get(f"repo.{n}"),
                        vendor=prop.get("vendor"), role=role)
    repos = sorted({r for n in everyone
                    if (r := prop.get("repo") if n == name else prop.get(f"repo.{n}"))})
    return True, (f"team {name}: {', '.join(everyone)} are agents. {said}"
                  + "".join(register_repo(r) for r in repos))


def _still_allowed_by(t, frm: str, to: str) -> str:
    """The edge that lets `frm` ask `to` in `t` once the exact edge is gone, or
    "" when nothing does - a wildcard or team edge can route the same pair, so
    removing one edge is not always taking the permission away."""
    for e in t.edges:
        if str(e.get("from")) == frm and str(e.get("to")) == to:
            continue
        if t.matches(str(e["from"]), frm) and t.matches(str(e["to"]), to):
            d = t.route(frm, to)
            return f"{e['from']} -> {e['to']}" if d.allowed else ""
    return ""


def _unlink_caveat(t, frm: str, to: str) -> str:
    left = _still_allowed_by(t, frm, to)
    return (f" {frm} can still ask {to}: the edge {left} allows it - "
            "this does not take the permission away." if left else "")


def _edge_refusal(kind: str, frm: str, to: str | None, response: str | None,
                  relation: str | None, registered: list[str]) -> str:
    """Why this edge cannot be added or removed, or "" when it can. Read against
    the team.yaml as it is now; the write checks again, on the text it edits."""
    if not to or to not in registered:
        return f"{to or 'a target'} is not an agent (an edge is between two agents)"
    if to == frm:
        return "an agent cannot ask itself"
    if response and response not in team.VALID_RESPONSE:
        return f"response must be one of {', '.join(sorted(team.VALID_RESPONSE))}"
    if relation and not re.fullmatch(r"[\w.-]+", relation):
        return f"bad relation: {relation!r} (letters, digits, . _ - only)"
    try:
        t = team.Team.load()
    except Exception as e:  # noqa: BLE001
        return f"team.yaml cannot be read ({e})"
    exact = [e for e in t.edges if str(e.get("from")) == frm and str(e.get("to")) == to]
    if kind == "link":
        for end in (frm, to):
            if end not in t.agents:
                return f"{end} is not in team.yaml agents - add it there first"
        if exact:
            return f"team.yaml already has the edge {frm} -> {to}"
        for d in t.deny:
            if t.matches(str(d.get("from", "")), frm) and t.matches(str(d.get("to", "")), to):
                return (f"a deny in team.yaml covers {frm} -> {to} "
                        f"({d.get('reason') or 'no reason given'}); an edge would never apply")
    elif not exact:
        return f"team.yaml has no edge exactly {frm} -> {to} to remove"
    return ""


def _edge_lines(lines: list[str], head: int, end: int):
    """(index, from, to) of each one-line edge in the edges block."""
    out = []
    for i in range(head + 1, end):
        s = lines[i].strip()
        if not s.startswith("- {"):
            continue
        try:
            e = (load("e: " + s[1:].strip()) or {}).get("e")
        except ConfigError:
            continue
        if isinstance(e, dict):
            out.append((i, str(e.get("from", "")), str(e.get("to", ""))))
    return out


def _link_text(text: str, frm: str, to: str, response: str | None,
               relation: str | None, pattern: bool = False) -> str:
    """One edge added. It goes before the first edge with a wildcard or a team at
    an end, because the first match wins and a specific edge behind a wildcard
    one would never be reached."""
    _text_only("agent", frm)
    if not pattern:  # a `<team>.*` target is a pattern, not a name YAML could misread
        _text_only("agent", to)
    if relation and not re.fullmatch(r"[\w.-]+", relation):
        raise ConfigError(f"bad relation: {relation!r}")
    lines = text.splitlines()
    head, end = _block(lines, "edges:")
    found = _edge_lines(lines, head, end)
    if any(f == frm and t == to for _, f, t in found):
        raise ConfigError(f"team.yaml already has the edge {frm} -> {to}")
    names = set(paths.list_agents())
    broad = [i for i, f, t in found if f not in names or t not in names]
    at = broad[0] if broad else (found[-1][0] + 1 if found else head + 1)
    parts = [f'from: "{frm}"', f'to: "{to}"']
    if relation:
        parts.append(f"relation: {relation}")
    parts.append(f"response: {response or 'async'}")
    lines.insert(at, "  - { " + ", ".join(parts) + " }")
    return "\n".join(lines) + "\n"


def _unlink_text(text: str, frm: str, to: str) -> str:
    lines = text.splitlines()
    head, end = _block(lines, "edges:")
    gone = [i for i, f, t in _edge_lines(lines, head, end) if f == frm and t == to]
    if not gone:
        raise ConfigError(f"team.yaml has no one-line edge exactly {frm} -> {to} to remove")
    return "\n".join(l for i, l in enumerate(lines) if i not in gone) + "\n"


def _link(prop: dict, con) -> tuple[bool, str]:
    frm, to = prop["agent"], prop["to"]
    err = _edge_refusal("link", frm, to, prop.get("response"), prop.get("relation"),
                        paths.list_agents())
    if err:
        return False, err
    ok, said = _edit_team_yaml(
        lambda t: _link_text(t, frm, to, prop.get("response"), prop.get("relation")),
        verify=lambda tm: _need(tm.route(frm, to).allowed, f"{frm} -> {to} is not routable"))
    return ok, (f"{frm} may now ask {to}. {said}" if ok else said)


def _unlink(prop: dict, con) -> tuple[bool, str]:
    frm, to = prop["agent"], prop["to"]
    err = _edge_refusal("unlink", frm, to, None, None, paths.list_agents())
    if err:
        return False, err
    ok, said = _edit_team_yaml(
        lambda t: _unlink_text(t, frm, to),
        verify=lambda tm: _need(not any(str(e.get("from")) == frm and str(e.get("to")) == to
                                        for e in tm.edges), "the edge is still there"))
    if not ok:
        return False, said
    return True, f"the edge {frm} -> {to} is removed.{_unlink_caveat(team.Team.load(), frm, to)} {said}"


def _need(cond: bool, why: str) -> None:
    if not cond:
        raise ConfigError(why)


def _retire(prop: dict, con) -> tuple[bool, str]:
    name = prop["agent"]
    ok, said = _in_team_yaml(name, None, add=False)
    if not ok:
        return False, said
    # The scheduling row goes with it. Its requests and events stay - they are
    # the record of work that was done - but a retired agent that still has a
    # state is one every listing keeps drawing and nothing ever moves.
    con.execute("DELETE FROM agent_state WHERE agent = ?", (name,))
    src = paths.agent_dir(name)
    dest = paths.home() / "agents" / "archive" / f"{time.strftime('%Y-%m-%d')}-{name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        if dest.exists():
            dest = dest.with_name(dest.name + f"-{prop['id']}")
        src.rename(dest)
    sealed.forget(sealed.agent_key(name))
    return True, f"{name} is retired; its folder is at {dest}. {said}"


def _rename(prop: dict, con) -> tuple[bool, str]:
    """One identity becomes another, taking its whole history with it.

    An agent is its folder and its rows: the journal, the handoffs, the digest,
    every request it sent or was sent, and the state the daemon schedules on. A
    rename that moved only the folder would leave work addressed to a name
    nothing answers to, which is the same failure as renaming the founder and
    forgetting the mailbox.

    Not while it is running. `OCP_AGENT` is fixed at exec, so a live session
    would keep writing hook events under the old name and reading an inbox that
    no longer exists.
    """
    from ..session import observe as obs

    old, new = prop["agent"], prop["to"]
    live = obs.live_sessions()
    if live is None:
        return False, (f"could not confirm whether {old} has a session running "
                       f"just now - try again in a moment.")
    if live.get(old):
        return False, (f"{old} has a session running. Close it first "
                       f"(`ocp close {old}`, or `c` on the bridge) - an identity "
                       f"cannot change under a running session.")
    src, dest = paths.agent_dir(old), paths.agent_dir(new)
    if dest.exists():
        return False, f"{dest} already exists"

    try:
        before = team.Team.load()
    except Exception as e:  # noqa: BLE001
        return False, f"team.yaml cannot be read ({e}); nothing renamed"

    def same_edges(after) -> None:
        """Every edge that reached the old name must reach the new one.

        The text edit knows the shapes it was written for, and team.yaml is
        hand-written: a block-style edge, or a glob like `manager*`, is renamed
        by neither. Rather than trust the edit, compare what the table actually
        routes - and refuse an edit that quietly dropped an edge.
        """
        was = _edges_touching(before, old)
        now = _edges_touching(after, new)
        missing = was - now
        if missing:
            raise ConfigError(
                f"{len(missing)} edge(s) still name {old} in a shape this cannot "
                f"rewrite ({', '.join(sorted(missing)[:3])}) - rename it in "
                f"team.yaml by hand first")

    ok, said2 = _edit_team_yaml(lambda t: _rename_text(t, old, new, prop.get("team")),
                                present=new, absent=old, verify=same_edges)
    if not ok:
        return False, said2
    if src.exists():
        src.rename(dest)
    cfg = agents.agent_file(new)
    if cfg.exists():
        data = load(cfg.read_text())
        if isinstance(data, dict):
            data["name"] = new
            agents.write(new, data)
            sealed.forget(sealed.agent_key(old))

    con.execute("UPDATE agent_state SET agent = ? WHERE agent = ?", (new, old))
    con.execute("UPDATE requests SET requester = ? WHERE requester = ?", (new, old))
    con.execute("UPDATE requests SET target = ? WHERE target = ?", (new, old))
    con.execute("UPDATE events SET agent = ? WHERE agent = ?", (new, old))
    con.execute("UPDATE handoff SET agent = ? WHERE agent = ?", (new, old))
    con.execute("UPDATE cursors SET name = REPLACE(name, ?, ?) WHERE name LIKE ?",
                (old, new, f"%{old}%"))
    _, labelled = label(con, new)
    return True, (f"{old} is now {new}: folder, team.yaml, and every row that named it. "
                  f"{said2}" + (f" {labelled}" if labelled else ""))


# How long Orca gets to answer about a label: a rename must not wait on it the
# way a launch waits on a terminal.
LABEL_TIMEOUT_S = 30


def _orca_entry(path: str) -> dict | None:
    """Orca's entry for the checkout at `path`, or None."""
    from ..session import orca

    ok, out = orca.host_cli(["worktree", "show", "--worktree", f"path:{path}", "--json"],
                            timeout=LABEL_TIMEOUT_S)
    try:
        entry = json.loads(out)["result"]["worktree"] if ok else None
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    return entry if isinstance(entry, dict) else None


def label(con, agent: str) -> tuple[bool, str]:
    """Name the agent's Orca worktree for the agent. This is what the founder
    reads in Orca's sidebar, so it is what a rename has to change; the folder,
    its git branch and Claude's memory keys stay as they are. (True, "") when
    the agent has no worktree recorded; otherwise (whether Orca now shows the
    name, what to say about it) - the reply is read back, not assumed."""
    from ..session import orca
    from . import spawn

    row = db.get_agent_state(con, agent)
    path = spawn.worktree_path(row["worktree"] if row else None)
    if not path:
        return True, ""
    cmd = ("orca worktree set --worktree " + shlex.quote(f"path:{path}")
           + " --display-name " + shlex.quote(agent))
    if not orca.keeps_labels():
        return True, ""             # a host with no worktree labels has nothing to rename
    if not orca.has_labels():
        return False, f"Orca is not on PATH, so its label is not set: `{cmd}`"
    ok, out = orca.host_cli(["worktree", "set", "--worktree", f"path:{path}",
                             "--display-name", agent, "--json"], timeout=LABEL_TIMEOUT_S)
    try:
        ok = ok and bool(json.loads(out).get("ok"))
    except (json.JSONDecodeError, AttributeError):
        ok = False
    shown = (_orca_entry(path) or {}).get("displayName") if ok else None
    if not ok or shown != agent:
        return False, f"Orca would not set the label ({out.strip()[:160]}): `{cmd}`"
    return True, f"Orca now labels {path} {agent!r}"


def _settings(prop: dict, con) -> tuple[bool, str]:
    """Exactly the settings that were proposed, through the register path."""
    changes, problem = agent_settings.changes_for(prop["agent"], _sets_of(prop))
    if not changes and not problem.startswith(agent_settings.NOTHING):
        return False, problem
    return (True, f"{prop['agent']}'s settings were already so") if not changes \
        else agent_settings.save_settings(prop["agent"], changes)


_DOERS = {"add": _add, "settings": _settings, "retire": _retire, "rename": _rename,
          "link": _link, "unlink": _unlink, "team": _team}


def _edges_touching(t, name: str) -> set[str]:
    """Each edge this agent is an end of, as "<i>:<end>" - by position, so an
    edge is the same edge before and after the name in it changed."""
    out = set()
    for i, e in enumerate(t.edges):
        if t.matches(str(e.get("from", "")), name):
            out.add(f"{i}:from")
        if t.matches(str(e.get("to", "")), name):
            out.add(f"{i}:to")
    return out


def _in_team_yaml(name: str, team_name: str | None, add: bool) -> tuple[bool, str]:
    """Put the name in the routing table, or take it out.

    The edges are not touched. `manager -> dev.*` is a wildcard over a team, so
    joining a team is what grants the routing: the design's own claim that
    adding a developer is two lines, made true by writing those two lines
    rather than a new edge nobody reviewed.
    """
    if add:
        return _edit_team_yaml(lambda t: _add_text(t, name, team_name), present=name)
    return _edit_team_yaml(lambda t: _remove_text(t, name), absent=name)


def _edit_team_yaml(edit, present: str | None = None, absent: str | None = None,
                    verify=None) -> tuple[bool, str]:
    """Apply a text edit to team.yaml, and write it only if it is still valid.

    Edited as text because team.yaml is a hand-written policy file whose
    comments are the reasons each edge exists, and a round-trip through the
    loader would drop every one of them. The result is parsed and validated
    before it is written, and an unfamiliar shape is refused rather than
    guessed at.
    """
    p = paths.team_file()
    if not p.exists():
        return False, f"{p} does not exist - run `ocp team init` first"
    sealed.require_founder()
    text = p.read_text()
    sealed.check(sealed.TEAM, text, p)
    try:
        edited = edit(text)
    except ConfigError as e:
        return False, str(e)

    try:
        data = load(edited)
        if not isinstance(data, dict):
            raise ConfigError("top level is not a map")
        team.Team(data)                  # the table has to still be valid
        listed = data.get("agents") or {}
        if present and present not in listed:
            raise ConfigError(f"the edit did not take: {present} missing from agents")
        if absent and absent in listed:
            raise ConfigError(f"the edit did not take: {absent} still in agents")
        if verify:
            verify(team.Team(data))
    except Exception as e:  # noqa: BLE001
        return False, f"that edit would break team.yaml ({e}); nothing written"

    backup = p.with_suffix(".yaml.bak")
    backup.write_text(text)
    p.write_text(edited)
    sealed.seal(sealed.TEAM, edited)
    return True, f"team.yaml updated (previous kept as {backup.name})"


def _block(lines: list[str], header: str) -> tuple[int, int]:
    """(first, last) line indexes of a top-level block, exclusive of its header."""
    for i, line in enumerate(lines):
        if line.rstrip() == header:
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or lines[j].startswith((" ", "\t"))
                                      or lines[j].lstrip().startswith("#")):
                j += 1
            return i, j
    raise ConfigError(f"team.yaml has no `{header}` block")


def _text_only(what: str, value: str | None) -> None:
    """Refuse to write into team.yaml a name YAML would not read back as text.
    `_draft` refuses these when a proposal is made; this is at the write, which
    a proposal saved before that check, or edited by hand, reaches without it."""
    if value and not is_plain_text(value):
        raise ConfigError(f"bad {what} name: {value!r}. {agents.NOT_TEXT}")


def _add_text(text: str, name: str, team_name: str | None) -> str:
    _text_only("agent", name)
    lines = text.splitlines()
    head, end = _block(lines, "agents:")
    if any(re.match(rf"\s*{re.escape(name)}\s*:", ln) for ln in lines[head + 1:end]):
        raise ConfigError(f"{name} is already in team.yaml")
    lines.insert(end, f"  {name}: {{}}")
    text = "\n".join(lines) + "\n"
    return _join_team(text, name, team_name) if team_name else text


def _join_team(text: str, name: str, team_name: str) -> str:
    _text_only("agent", name)
    _text_only("team", team_name)
    lines = text.splitlines()
    head, end = _block(lines, "teams:")
    pattern = re.compile(rf"^(\s*{re.escape(team_name)}\s*:\s*\[)([^\]]*)(\].*)$")
    for i in range(head + 1, end):
        m = pattern.match(lines[i])
        if m:
            members = [x.strip() for x in m.group(2).split(",") if x.strip()]
            if name not in members:
                members.append(name)
            lines[i] = f"{m.group(1)}{', '.join(members)}{m.group(3)}"
            break
    else:
        lines.insert(end, f"  {team_name}: [{name}]")
    return "\n".join(lines) + "\n"


def _rename_text(text: str, old: str, new: str, team_name: str | None) -> str:
    """The name changed everywhere it is written, and nothing else.

    Everywhere means the edges too. A rename that moved only the team entry
    left `from: "manager"` naming nobody, and the renamed manager with no
    edge to the reports it supervises.
    """
    _text_only("agent", new)
    word = re.escape(old)
    lines = text.splitlines()
    head, end = _block(lines, "agents:")
    if not any(re.match(rf"\s*{word}\s*:", ln) for ln in lines[head + 1:end]):
        raise ConfigError(f"{old} is not in team.yaml's agents block")
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            continue
        if head < i < end:
            line = re.sub(rf"^(\s*){word}(\s*:)", rf"\g<1>{new}\2", line)
        m = re.match(r"^(\s*\w[\w.-]*\s*:\s*\[)([^\]]*)(\].*)$", line)
        if m:
            members = [x.strip() for x in m.group(2).split(",") if x.strip()]
            members = [new if x == old else x for x in members]
            if team_name:
                members = [x for x in members if x != new]
            line = f"{m.group(1)}{', '.join(members)}{m.group(3)}"
        line = re.sub(rf"""(?<![\w-])((?:from|to)\s*:\s*)(["']?){word}\2(?=\s*[,}}])""",
                      rf"\g<1>\g<2>{new}\g<2>", line)
        lines[i] = line
    text = "\n".join(lines) + "\n"
    if team_name:
        text = _join_team(text, new, team_name)
    return text


def _remove_text(text: str, name: str) -> str:
    lines = text.splitlines()
    out = []
    dropped = False
    head, end = _block(lines, "agents:")
    for i, line in enumerate(lines):
        if head < i < end and re.match(rf"\s*{re.escape(name)}\s*:", line):
            dropped = True
            continue
        m = re.match(r"^(\s*\w[\w.-]*\s*:\s*\[)([^\]]*)(\].*)$", line)
        if m and name in [x.strip() for x in m.group(2).split(",")]:
            members = [x.strip() for x in m.group(2).split(",")
                       if x.strip() and x.strip() != name]
            line = f"{m.group(1)}{', '.join(members)}{m.group(3)}"
        out.append(line)
    if not dropped:
        raise ConfigError(f"{name} is not in team.yaml's agents block")
    return "\n".join(out) + "\n"
