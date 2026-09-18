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

import re
import time
import uuid
from pathlib import Path

from . import agents, ask as askmod, db, paths, team
from .yamlcompat import ConfigError, dump, load

# What the founder types to decide. Printed in the escalation, so the words are
# in front of whoever answers rather than in a document they have not read.
APPROVE = ("approve", "approved", "yes", "ok", "go", "승인")
REJECT = ("reject", "rejected", "no", "deny", "denied", "반려")

KINDS = ("add", "retire")


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


def listing() -> list[dict]:
    out = []
    for f in sorted(dir_().glob("*.yaml")):
        data = load(f.read_text())
        if isinstance(data, dict):
            out.append(data)
    return out


def propose(kind: str, name: str, requester: str, why: str, account: str | None = None,
            team_name: str | None = None, repo: str | None = None,
            model: str | None = None) -> tuple[bool, str]:
    """Write the proposal down and raise it to the founder. Nothing changes yet."""
    if kind not in KINDS:
        return False, f"kind must be one of {', '.join(KINDS)}"
    if not name or "/" in name or name.startswith("."):
        return False, f"bad agent name: {name!r}"
    if not (why or "").strip():
        return False, "say why: the founder is being asked to decide, not to rubber-stamp"

    registered = paths.list_agents()
    if kind == "add" and name in registered:
        return False, f"{name} is already an agent"
    if kind == "retire" and name not in registered:
        return False, f"{name} is not an agent"
    if kind == "add":
        if not account:
            return False, "an added agent needs an account (--account)"
        if account not in paths.list_accounts():
            return False, (f"account '{account}' is not registered. A human logs an "
                           f"account in once; ocp never touches credentials.")

    prop = {
        "id": uuid.uuid4().hex[:8],
        "kind": kind,
        "agent": name,
        "requested_by": requester,
        "why": why.strip(),
        "created": int(time.time()),
        "state": "proposed",
    }
    for key, value in (("account", account), ("team", team_name), ("repo", repo),
                       ("model", model)):
        if value:
            prop[key] = value

    ok, said, rid = askmod.ask(askmod.HUMAN, _escalation(prop), requester=requester)
    if not ok:
        return False, f"could not raise it: {said}"
    prop["request"] = rid
    save(prop)
    return True, (f"proposal {prop['id']} raised as #{rid}. Nothing changes until the "
                  f"founder answers it; then `ocp hr apply {prop['id']}`.")


def _escalation(prop: dict) -> str:
    """What the founder reads. It says what will happen and how to decide."""
    lines = [f"[roster] {prop['kind']} {prop['agent']} - asked by {prop['requested_by']}",
             "", prop["why"], ""]
    if prop["kind"] == "add":
        lines += [f"It would be registered on account {prop.get('account')}"
                  + (f", in team {prop['team']}" if prop.get("team") else "")
                  + (f", working in {prop['repo']}" if prop.get("repo") else "")
                  + (f", model {prop['model']}" if prop.get("model") else "") + ".",
                  "team.yaml gains its name; nothing else in the routing changes."]
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
    prop = load_proposal(pid)
    if not prop:
        return False, f"no proposal {pid}"
    if prop.get("state") == "applied":
        return False, f"proposal {pid} was already applied on {_when(prop.get('applied'))}"
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
            return False, f"proposal {pid} was rejected: {(verdict or '').strip()[:120]}"
        if call != "approved":
            return False, (f"#{row['id']} was answered {(verdict or '').strip()[:60]!r}, "
                           f"which is not a decision. Ask the founder to answer "
                           f"`approve` or `reject`.")
        ok, said = (_add(prop) if prop["kind"] == "add" else _retire(prop))
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


def _when(ts) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "an unknown date"


def _add(prop: dict) -> tuple[bool, str]:
    name = prop["agent"]
    agents.register(name, prop["account"], model=prop.get("model"),
                    repo=prop.get("repo"),
                    role=f"Added on {_when(prop.get('created'))} at "
                         f"{prop['requested_by']}'s request: {prop['why']}")
    ok, said = _in_team_yaml(name, prop.get("team"), add=True)
    if not ok:
        return False, said
    return True, (f"{name} is an agent: account {prop['account']}"
                  + (f", team {prop['team']}" if prop.get("team") else "")
                  + f". {said}")


def _retire(prop: dict) -> tuple[bool, str]:
    name = prop["agent"]
    ok, said = _in_team_yaml(name, None, add=False)
    if not ok:
        return False, said
    src = paths.agent_dir(name)
    dest = paths.home() / "agents" / "archive" / f"{time.strftime('%Y-%m-%d')}-{name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        if dest.exists():
            dest = dest.with_name(dest.name + f"-{prop['id']}")
        src.rename(dest)
    return True, f"{name} is retired; its folder is at {dest}. {said}"


def _in_team_yaml(name: str, team_name: str | None, add: bool) -> tuple[bool, str]:
    """Put the name in the routing table, or take it out - as text.

    The edges are not touched. `manager -> dev.*` is a wildcard over a team, so
    joining a team is what grants the routing: the design's own claim that
    adding a developer is two lines, made true by writing those two lines
    rather than a new edge nobody reviewed.

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
        edited = _add_text(text, name, team_name) if add else _remove_text(text, name)
    except ConfigError as e:
        return False, str(e)

    try:
        data = load(edited)
        if not isinstance(data, dict):
            raise ConfigError("top level is not a map")
        team.Team(data)                  # the table has to still be valid
        got = name in (data.get("agents") or {})
        if got is not add:
            raise ConfigError(f"the edit did not take: {name} "
                              f"{'missing from' if add else 'still in'} agents")
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


def _add_text(text: str, name: str, team_name: str | None) -> str:
    lines = text.splitlines()
    head, end = _block(lines, "agents:")
    if any(re.match(rf"\s*{re.escape(name)}\s*:", ln) for ln in lines[head + 1:end]):
        raise ConfigError(f"{name} is already in team.yaml")
    lines.insert(end, f"  {name}: {{ intake: dedicated }}")

    if team_name:
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
