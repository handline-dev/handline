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
    escalation it was raised as, and what happened to it. A crash between the
    approval and the change leaves the proposal there to be applied again.
  - The approval is read, not interpreted. The verdict's first word decides,
    out of a vocabulary the escalation itself prints. A model reading "maybe
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

from . import agents, ask as askmod, db, paths, team, textsafe, vendors
from .yamlcompat import ConfigError, dump, is_plain_text, load

# What the founder types to decide. Printed in the escalation, so the words are
# in front of whoever answers rather than in a document they have not read.
APPROVE = ("approve", "approved", "yes", "ok", "go", "승인")
REJECT = ("reject", "rejected", "no", "deny", "denied", "반려")

KINDS = ("add", "retire", "rename")


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
    tmp = p.with_suffix(".tmp")
    tmp.write_text(dump(prop))
    tmp.replace(p)
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
           vendor: str | None = None) -> tuple[dict, str]:
    """Check what was asked for and write it down. Returns (proposal, refusal)."""
    if kind not in KINDS:
        return {}, f"kind must be one of {', '.join(KINDS)}"
    if not name or "/" in name or name.startswith("."):
        return {}, f"bad agent name: {name!r}"
    if not is_plain_text(name):
        return {}, f"bad agent name: {name!r}. {agents.NOT_TEXT}"
    if not (why or "").strip():
        return {}, "say why: the founder is being asked to decide, not to rubber-stamp"

    registered = paths.list_agents()
    if kind == "add" and name in registered:
        return {}, f"{name} is already an agent"
    if kind in ("retire", "rename") and name not in registered:
        return {}, f"{name} is not an agent"
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
                       ("effort", effort), ("to", to), ("vendor", vendor)):
        if value:
            prop[key] = value
    return prop, ""


def propose(kind: str, name: str, requester: str, why: str,
            team_name: str | None = None, repo: str | None = None,
            model: str | None = None, to: str | None = None,
            effort: str | None = None, vendor: str | None = None) -> tuple[bool, str]:
    """Write the proposal down and raise it to the founder. Nothing changes yet."""
    prop, err = _draft(kind, name, requester, why, team_name, repo, model,
                       to, effort, vendor)
    if err:
        return False, err
    # Written down before it is raised: a proposal that cannot be recorded must
    # not leave a request in front of the founder that names one.
    try:
        dump(prop)
    except ConfigError as e:
        return False, f"could not write the proposal down, so nothing was raised: {e}"
    ok, said, rid = askmod.ask(askmod.HUMAN, _escalation(prop), requester=requester)
    if not ok:
        return False, f"could not raise it: {said}"
    prop["request"] = rid
    save(prop)
    return True, (f"proposal {prop['id']} raised as #{rid}. Nothing changes until the "
                  f"founder answers it; then `ocp hr apply {prop['id']}`.")


def enact(kind: str, name: str, why: str,
          team_name: str | None = None, repo: str | None = None,
          model: str | None = None, to: str | None = None,
          effort: str | None = None, con=None,
          vendor: str | None = None) -> tuple[bool, str]:
    """The founder changing the roster directly. No proposal to approve.

    The gate exists to stop an *agent* hiring on its own, and the founder is the
    thing on the other side of it: asking them to approve a request they just
    typed is ceremony, and it is also the only way to make the first agent -
    a proposal needs an agent to raise it, and an empty roster has none.

    The file is written before anything changes, as a proposal's is, so a
    change that fails halfway still leaves a record of what was attempted.
    """
    prop, err = _draft(kind, name, "human", why, team_name, repo, model,
                       to, effort, vendor)
    if err:
        return False, err
    prop["state"] = "enacting"
    save(prop)
    own = con is None
    con = con or db.connect()
    try:
        doer = {"add": _add, "retire": _retire, "rename": _rename}[kind]
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


def _escalation(prop: dict) -> str:
    """What the founder reads. It says what will happen and how to decide."""
    lines = [f"[roster] {prop['kind']} {prop['agent']} - asked by {prop['requested_by']}",
             "", prop["why"], ""]
    if prop["kind"] == "add":
        lines += ["It would be registered"
                  + (f" in team {prop['team']}" if prop.get("team") else "")
                  + (f", working in {prop['repo']}" if prop.get("repo") else "")
                  + (f", model {prop['model']}" if prop.get("model") else "")
                  + (f", vendor {prop['vendor']}"
                     if prop.get("vendor") and prop["vendor"] != agents.DEFAULT_VENDOR else "")
                  + ".",
                  "team.yaml gains its name; nothing else in the routing changes."]
    elif prop["kind"] == "rename":
        lines += [f"{prop['agent']} would become {prop.get('to')}: its folder, its "
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
        return False, f"proposal {pid} was already applied on {_when(prop.get('applied'))}"
    if prop.get("state") == "withdrawn":
        return False, f"proposal {pid} was withdrawn"
    own = con is None
    con = con or db.connect()
    try:
        row = db.get_request(con, int(prop.get("request") or 0))
        if row is None:
            return False, f"proposal {pid} has no escalation to check - raise it again"
        if row["state"] == "queued":
            return False, (f"the founder has not answered #{row['id']} yet. It is on "
                           f"`ocp bridge`, top panel.")
        verdict = row["verdict"]
        call = decision(verdict)
        if call == "rejected":
            prop["state"] = "rejected"
            prop["verdict"] = verdict
            save(prop)
            # The founder's own text, printed raw by cmd_hr - sanitised here
            # rather than trusted to save()'s control-character refusal above,
            # which does not cover the C1 range (auditor, #1048).
            return False, (f"proposal {pid} was rejected: "
                           f"{textsafe.oneline((verdict or '').strip())[:120]}")
        if call != "approved":
            return False, (f"#{row['id']} was answered "
                           f"{(db.shown_verdict(row) or '').strip()[:60]!r}, "
                           f"which is not a decision. Ask the founder to answer "
                           f"`approve` or `reject`, or `ocp hr withdraw {pid}`.")
        doer = {"add": _add, "retire": _retire, "rename": _rename}[prop["kind"]]
        ok, said = doer(prop, con)
        if not ok:
            return False, said
        prop["state"] = "applied"
        prop["applied"] = int(time.time())
        prop["verdict"] = verdict
        save(prop)
        return True, said
    finally:
        if own:
            con.close()


class _Unwound(Exception):
    """A withdraw that had to be taken back, with the reason to show."""


def withdraw(pid: str, me: str, con=None) -> tuple[bool, str]:
    """The proposal's own author, or the founder, takes it back.

    The way out of a proposal that will get no decision: an escalation closed
    without approve or reject leaves `apply` refusing it and nothing else able
    to end it. A still-queued escalation is closed with it and marked told:
    the proposer is not messaged, whoever withdrew.

    A withdraw and the founder's answer settle to exactly one outcome, decided
    under one write lock. The escalation is read there: an approve or reject
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
            row = (db.get_request(con, int(prop["request"]))
                   if int(prop.get("request") or 0) else None)
            if row is not None and row["state"] != "queued" \
                    and decision(row["verdict"]) != "unclear":
                con.execute("ROLLBACK")
                return False, (f"the founder already answered #{row['id']} "
                               f"({textsafe.oneline((row['verdict'] or '').strip())[:60]!r}) - "
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


def is_open_proposal(rid: int) -> bool:
    """Is request `rid` the escalation of a proposal still waiting on approve or reject?"""
    return any(p.get("state") == "proposed" and int(p.get("request") or 0) == rid
               for p in listing())


def _when(ts) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "an unknown date"


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
                    role=f"Added on {_when(prop.get('created'))} at "
                         f"{prop['requested_by']}'s request: {prop['why']}")
    return True, (f"{name} is an agent"
                  + (f", team {prop['team']}" if prop.get("team") else "")
                  + f". {said}")


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
    from . import observe as obs

    old, new = prop["agent"], prop["to"]
    live = obs.live_sessions()
    if live and live.get(old):
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
            cfg.write_text(dump(data))

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
    from . import orca

    ok, out = orca._run(["worktree", "show", "--worktree", f"path:{path}", "--json"],
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
    from . import orca, spawn

    row = db.get_agent_state(con, agent)
    path = spawn.worktree_path(row["worktree"] if row else None)
    if not path:
        return True, ""
    cmd = ("orca worktree set --worktree " + shlex.quote(f"path:{path}")
           + " --display-name " + shlex.quote(agent))
    if not orca.available():
        return False, f"Orca is not on PATH, so its label is not set: `{cmd}`"
    ok, out = orca._run(["worktree", "set", "--worktree", f"path:{path}",
                         "--display-name", agent, "--json"], timeout=LABEL_TIMEOUT_S)
    try:
        ok = ok and bool(json.loads(out).get("ok"))
    except (json.JSONDecodeError, AttributeError):
        ok = False
    shown = (_orca_entry(path) or {}).get("displayName") if ok else None
    if not ok or shown != agent:
        return False, f"Orca would not set the label ({out.strip()[:160]}): `{cmd}`"
    return True, f"Orca now labels {path} {agent!r}"


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
    text = p.read_text()
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
