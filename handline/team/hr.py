"""Changing the roster, which is the one thing an agent may not simply do.

A team that cannot grow has one way to take on more work: each agent quietly
making helpers of its own, which die with the session that made them and appear
on no screen. So growing is a first-class operation - and it is the operation
with the least business being automatic, because an agent that can hire can
spend an account into the ground without anyone deciding to.

The shape is therefore: any agent may *propose*, only the founder may decide, and an approve
is applied by the same call that records it. A proposal is not a task: it is its own record, and
the founder decides it from a chat card or `ocp hr approve|reject`. Three properties fall out:

  - It is a file. `hr/<id>.yaml` holds what was asked for, who asked, and what happened to it
    (proposed, approved, applied, rejected, withdrawn). A crash between the approval and the
    change leaves it approved, to be applied again.
  - The approval is a call, not a sentence. The founder's decision is recorded where no agent can
    write it, and applying acts on that record for the draft as it stood.
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
    """Write the proposal down for the founder to decide (`decide`). Nothing changes yet."""
    prop, err = _draft(kind, name, requester, why, team_name, repo, model,
                       to, effort, vendor, response, relation, members, repos, sets)
    if err:
        return False, err
    # Written down before it is raised: a proposal that cannot be recorded must not be
    # waiting in front of the founder.
    try:
        dump(prop)
    except ConfigError as e:
        return False, f"could not write the proposal down, so nothing was raised: {e}"
    save(prop)
    return True, (f"proposal {prop['id']} raised. Nothing changes until the founder decides it; "
                  f"an approve is applied then, and you are told how it went.")


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
                  + ". team.yaml gains the team and the agents, no edge; the default links "
                  "apply:"]
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
    lines += ["", f"Approve or reject: `ocp hr approve {prop['id']}` / `ocp hr reject {prop['id']}`."]
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


def card(prop: dict) -> dict:
    """A proposal as the founder's chat card and `ocp hr list --json` show it. `text` is what
    will happen if it is approved; `state` is "proposed" (waiting on the founder) or "approved"
    (decided, but the change did not go through - `error` says why, and approving again retries).
    `subject` is the digest to hand back to `decide`: what was read is what is approved. `verdict` and
    `at` say how and when a settled one ended (see `decided`)."""
    return {"id": prop["id"], "kind": prop["kind"], "agent": prop["agent"],
            "to": prop.get("to"), "requested_by": prop.get("requested_by"),
            "why": prop.get("why"), "ts": int(prop.get("created") or 0),
            "state": prop.get("state"), "error": prop.get("error"),
            "text": _decision_text(prop), "subject": subject_of(prop),
            "verdict": prop.get("verdict"),
            "at": int(prop.get("applied") or prop.get("decided") or prop.get("created") or 0)}


def pending() -> list[dict]:
    """The proposals still waiting on the founder, oldest first."""
    return [card(p) for p in sorted(listing(), key=lambda p: p.get("created") or 0)
            if p.get("state") in ("proposed", "approved")]


def decided(since: int = 0) -> list[dict]:
    """The proposals that are settled (rejected, applied, withdrawn) at or after `since`, oldest
    first: what a chat card draws as the outcome of the one the founder decided."""
    out = [card(p) for p in listing() if p.get("state") in ("rejected", "applied", "withdrawn")]
    return sorted((c for c in out if c["at"] >= since), key=lambda c: (c["at"], c["id"]))


def _open(pid: str) -> tuple[dict | None, str]:
    """(proposal, refusal) for a proposal that can be read at all."""
    try:
        prop = load_proposal(pid)
    except (ConfigError, UnicodeDecodeError, OSError) as e:
        return None, f"proposal {pid} cannot be read: {e}"
    if not prop:
        return None, f"no proposal {pid}"
    if not is_proposal(prop):
        return None, f"{pid} is not a proposal this process knows"
    return prop, ""


def _tell(prop: dict, text: str) -> None:
    """Tell the proposer how it went. A message that cannot be delivered never undoes the decision."""
    who = prop.get("requested_by")
    if who and who != askmod.HUMAN:
        askmod.say(who, text, requester=askmod.HUMAN)


def decide(pid: str, call: str, by: str, note: str = "", subject: str = "") -> tuple[bool, str]:
    """The founder's decision on a proposal: `call` is "approved" or "rejected". An approve is
    applied now and the proposer is told the outcome; a reject is recorded and told.

    The decision is recorded in the protected store, where no agent can write it, and `apply`
    acts on that record alone - so only the founder's own session can decide (`by` is the
    caller's claim; the store is the proof). An approve needs `subject`, the card's digest the
    founder read: a draft that is not that one is refused, so an edit made after it was shown is
    never approved unseen (the file is writable by every agent). Approving a proposal that was approved but did not apply retries it."""
    if call not in ("approved", "rejected"):
        return False, "decide `approved` or `rejected`"
    if by != askmod.HUMAN:
        return False, f"proposal {pid} is the founder's to decide, not {by}'s"
    if call == "approved" and not subject:
        return False, "approve what was read: pass the card's `subject`"
    note = textsafe.oneline((note or "").strip())
    prop, err = _open(pid)
    if err:
        return False, err
    con = db.connect()
    try:
        # One decision wins: the state is read, the answer recorded and the state set under one
        # write lock, so a withdraw or a second answer finds this one already there.
        con.execute("BEGIN IMMEDIATE")
        try:
            prop = load_proposal(pid) or prop
            if prop.get("state") not in ("proposed", "approved"):
                con.execute("ROLLBACK")
                return False, f"proposal {pid} is already {prop.get('state')}"
            if call == "approved" and subject != subject_of(prop):
                con.execute("ROLLBACK")
                return False, (f"proposal {pid} changed after you read it - read it again "
                               f"before approving")
            got = decisions.get("hr", pid)
            if (prop.get("state") == "approved" and call == "approved" and got
                    and got.get("call") == "approved" and got.get("subject") == subject_of(prop)):
                con.execute("ROLLBACK")
                return apply(pid)
            try:
                decisions.record("hr", pid, 0, call, subject_of(prop))
            except PermissionError as e:
                con.execute("ROLLBACK")
                return False, str(e)
            prop["state"] = call
            prop["verdict"] = note or call
            prop["decided"] = int(time.time())
            save(prop)
            con.execute("COMMIT")
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
    finally:
        con.close()
    if call == "rejected":
        _tell(prop, f"proposal {pid} was rejected" + (f": {note}" if note else "."))
        return True, f"proposal {pid} rejected"
    return apply(pid)


def apply(pid: str, con=None) -> tuple[bool, str]:
    """Carry out a proposal the founder approved. Refuses anything else, and anything whose
    recorded approval is not for the draft as it is now."""
    prop, err = _open(pid)
    if err:
        return False, err
    state = prop.get("state")
    if state == "applied":
        return False, (f"proposal {pid} was already applied on "
                       f"{timefmt.stamp(prop.get('applied'), timefmt.DATE_TIME, 'an unknown date')}")
    if state != "approved":
        return False, (f"proposal {pid} is {state}, not approved - "
                       f"the founder decides it with `ocp hr approve {pid}`")
    got = decisions.get("hr", pid)
    if got is None or got.get("call") != "approved":
        return False, (f"no approval from the founder is on record for proposal {pid} - "
                       f"it was not decided by the founder; raise it again")
    if got.get("subject") != subject_of(prop):
        return False, (f"proposal {pid} was changed after the founder approved it - "
                       f"what they approved is not what is in the draft now; raise it again")
    own = con is None
    con = con or db.connect()
    try:
        ok, said = _DOERS[prop["kind"]](prop, con)
        if not ok:
            prop["error"] = said
            save(prop)
            _tell(prop, f"proposal {pid} was approved but could not be applied: {said}")
            return False, said
        prop["state"] = "applied"
        prop["applied"] = int(time.time())
        prop.pop("error", None)
        save(prop)
        _tell(prop, f"proposal {pid} applied: {said}")
        return True, said
    finally:
        if own:
            con.close()


def withdraw(pid: str, me: str) -> tuple[bool, str]:
    """The proposal's own author, or the founder, takes it back before it is applied."""
    prop, err = _open(pid)
    if err:
        return False, err
    if me not in (prop.get("requested_by"), askmod.HUMAN):
        return False, f"proposal {pid} is {prop.get('requested_by')}'s to withdraw, not yours"
    con = db.connect()
    try:
        con.execute("BEGIN IMMEDIATE")      # against a decision landing at the same moment
        try:
            prop = load_proposal(pid) or prop
            if prop.get("state") not in ("proposed", "approved"):
                con.execute("ROLLBACK")
                return False, f"proposal {pid} is already {prop.get('state')} - nothing to withdraw"
            prop["state"] = "withdrawn"
            prop["decided"] = int(time.time())
            save(prop)
            con.execute("COMMIT")
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
    finally:
        con.close()
    return True, f"proposal {pid} withdrawn"


def leave_tasks(con) -> list[str]:
    """Proposals raised before they stopped being tasks: each still carries a decision task and
    its card request. An open one is taken off the founder's task board (the card closed, the task
    discarded with it) and stays a proposal, now waiting on `decide`. One the founder already
    answered keeps that answer if it is on record for the draft as it stands (approved, or
    rejected); any other is left to be decided again. Returns the ids of the proposals moved."""
    moved = []
    for prop in listing():
        if prop.get("state") != "proposed" or not (prop.get("task") or prop.get("request")):
            continue
        state, tid, verdict = decisions.answer_of(con, prop)
        if state == "open":
            card = decisions.card_of(con, tid)
            if card is not None:
                db.close_if_queued(con, card["id"], "withdrawn", "moved to the founder's hr requests",
                                   announced=True)
        elif state == "answered":
            got = decisions.get("hr", prop["id"])
            if (got and got.get("call") in ("approved", "rejected")
                    and decisions.call_of(verdict) == got["call"]
                    and got.get("subject") == subject_of(prop)):
                prop["state"] = got["call"]
                prop["verdict"] = textsafe.oneline(verdict or "")
        prop.pop("task", None)
        prop.pop("request", None)
        save(prop)
        moved.append(prop["id"])
    return moved


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
    """A whole team in one approval: the manager and its members in team.yaml (their
    links are `team.DEFAULT_LINKS`, no edge), then each one registered. team.yaml is written first, as `_add` does, and once."""
    name, members = prop["agent"], _members_of(prop)
    everyone = [name, *members]

    def edit(text: str) -> str:
        for n in everyone:
            text = _add_text(text, n, name)
        return text

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
