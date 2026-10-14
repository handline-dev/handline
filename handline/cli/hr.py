"""`hl hr` and `hl trust`."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from ..team import ask as askmod
from .. import db
from ..team import hr
from ..team import rehome
from ..team import trust
from ..infra import daemonclient as dmn_client, protected, textsafe

from . import client
from .ask import _who_refusal
from .health import _name_col


def _read_changes(path: str) -> tuple[list[dict], str]:
    """The `changes:` of a team-diff file, with a file named by `role_file` / `base_file` read in as the
    new text of a `context` change. (changes, refusal)."""
    from ..infra.yamlcompat import ConfigError, load

    base = Path(path).expanduser()
    try:
        data = load(base.read_text())
    except (OSError, UnicodeDecodeError, ConfigError) as e:
        return [], f"{path}: {e}"
    changes = data.get("changes") if isinstance(data, dict) else None
    if not isinstance(changes, list):
        return [], f"{path}: needs a `changes:` list"
    out = []
    for ch in changes:
        if not isinstance(ch, dict):
            return [], f"{path}: each change is a map"
        ch = dict(ch)
        texts = dict(ch.get("sets") or {})
        for key, fn in (("role_file", "ROLE.md"), ("base_file", "base.md")):
            if key in ch:
                try:
                    texts[fn] = (base.parent / str(ch.pop(key))).expanduser().read_text()
                except (OSError, UnicodeDecodeError) as e:
                    return [], f"{path}: {e}"
        if texts:
            ch["sets"] = texts
        if isinstance(ch.get("members"), str):
            ch["members"] = [m for m in ch["members"].split(",") if m]
        out.append(ch)
    return out, ""


def propose_for_session(kind: str, name: str, sets: dict, why: str | None) -> int:
    """What a founder's command does when an agent runs it: the change is raised as an HR proposal
    of `kind` for the founder to approve, and nothing changes yet."""
    if not (why or "").strip():
        print(f"from an agent's session this asks the founder: say why with --why", file=sys.stderr)
        return 1
    ok, said = hr.propose(kind, name, requester=askmod.asking_agent(), why=why, sets=sets)
    print(said)
    return 0 if ok else 1


def _waiting() -> list[dict]:
    """The proposals waiting on the founder, as hr.list serves them."""
    return [p for p in client.call("hr.list")["proposals"] if p["state"] in ("open", "apply_failed")]


def _wire_id(p: dict) -> str:
    """A served proposal's id as its file and `hl hr` name it."""
    return f"{p['id']:08x}"


def _number(pid: str) -> int | None:
    """The wire's number for a proposal's id (hex text), None for an id that is none."""
    try:
        return int(pid, 16)
    except ValueError:
        return None


def _decide(pid: str, decision: str, subject: str = "", note: str = "") -> tuple[bool, str]:
    """hr.decide on one proposal: the daemon applies an approve and tells the proposer."""
    if _number(pid) is None:
        return False, f"no proposal {pid}"
    params = {"proposal": _number(pid), "decision": decision}
    if subject:
        params["subject"] = subject
    if note:
        params["note"] = note
    return client.write("hr.decide", params)


def _decide_each(entries: list[tuple[str, str]], decision: str, note: str = "") -> int:
    """hr.decide on each (id, subject) in order, one line each; each proposer is told of each."""
    oks = []
    for pid, subject in entries:
        ok, said = _decide(pid, decision, subject, note)
        print(f"{pid}: {said.splitlines()[0] if said else ''}")
        oks.append(ok)
    return 0 if all(oks) else 1


def _approve_many(ids: list[str], yes: bool) -> int:
    """The founder approving several proposals at once: each is read as it stands now, shown, and
    confirmed once; they are applied one after another."""
    entries = []
    for pid in ids:
        prop, err = hr._open(pid)
        if err:
            print(err)
            return 1
        shown = hr.card(prop)
        print("\n".join(textsafe.drawable(shown["text"])))
        print()
        entries.append((pid, shown["subject"]))
    if not yes:
        if not sys.stdin.isatty():
            print("not a terminal: pass --yes to approve them as shown", file=sys.stderr)
            return 1
        if input(f"approve all {len(entries)}? [y/N] ").strip().lower() not in ("y", "yes"):
            print("left alone")
            return 1
    return _decide_each(entries, "approve")


# ---------------------------------------------------------------- team / ask
def cmd_hr(args) -> int:
    """Propose a change to the roster, see the proposals, or apply an approved one.

    Any agent may propose; only the founder approves; the change is made from
    the file the proposal was written to. Hiring is the one thing an agent must
    not be able to do on its own - an agent that can hire can spend an account
    into the ground with nobody having decided to.
    """
    me = askmod.asking_agent()
    if args.hr_cmd == "list":
        try:
            rows = client.call("hr.list")["proposals"] if args.all or not args.json else _waiting()
        except (dmn_client.DaemonDown, dmn_client.RpcError, OSError) as e:
            print(f"the proposals cannot be read: {e}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(rows, indent=2))
            return 0
        if not rows:
            print("no proposals")
            return 0
        for p_ in rows:
            print(f"{_wire_id(p_)}  {p_['kind']:<7}"
                  f"{textsafe.oneline(str(p_['agent'])):<{_name_col([r['agent'] for r in rows])}}"
                  f"{p_['state']:<13}"
                  f"by {textsafe.oneline(str(p_.get('requested_by', '?')))}")
        return 0
    if args.hr_cmd == "relabel":
        con = db.connect()
        try:
            ok, said = hr.label(con, args.id)
        finally:
            con.close()
        print(said or f"{args.id} has no worktree recorded - nothing to label")
        return 0 if ok else 1
    if args.hr_cmd == "rehome":
        con = db.connect()
        try:
            ok, said = rehome.move(con, args.id, args.old)
        finally:
            con.close()
        print(said)
        return 0 if ok else 1
    if args.hr_cmd == "links":
        from ..team import linkmigrate
        ok, said = linkmigrate.migrate(args.apply)
        print(said)
        return 0 if ok else 1
    if args.hr_cmd == "apply":
        prop, err = hr._open(args.id)
        if err:
            print(err)
            return 1
        if prop.get("state") != "approved":
            print(f"proposal {args.id} is {prop.get('state')}, not approved - "
                  f"the founder decides it with `hl hr approve {args.id}`")
            return 1
        return client.said(*_decide(args.id, "approve", hr.subject_of(prop)))
    if args.hr_cmd in ("approve", "reject"):
        if protected.in_agent_session() or me != askmod.HUMAN:
            print("approving or rejecting a proposal is the founder's, from a terminal or the "
                  "screen, not an agent's", file=sys.stderr)
            return 1
        if args.all and (getattr(args, "ids", None) or getattr(args, "id", None)):
            print(f"name {'proposals' if args.hr_cmd == 'approve' else 'a proposal'}, or --all, not both", file=sys.stderr)
            return 1
        if args.all:
            try:
                waiting = _waiting()
            except (dmn_client.DaemonDown, dmn_client.RpcError, OSError) as e:
                print(f"the proposals cannot be read: {e}", file=sys.stderr)
                return 1
            if not waiting:
                print("no proposals")
                return 0
            if args.hr_cmd == "reject":
                # A reject changes nothing, and its proposer may raise it again, so it is not asked twice.
                return _decide_each([(_wire_id(p), "") for p in waiting], "reject", args.all_reason)
            return _approve_many([_wire_id(p) for p in waiting], args.yes)
        if args.hr_cmd == "approve":
            if not args.ids:
                print("name the proposals to approve, or --all", file=sys.stderr)
                return 1
            if len(args.ids) > 1:
                return _approve_many(list(args.ids), args.yes)
            args.id = args.ids[0]
        elif not args.id:
            print("name the proposal to reject, or --all", file=sys.stderr)
            return 1
        subject = getattr(args, "subject", "")
        if args.hr_cmd == "approve" and not subject:
            prop, err = hr._open(args.id)
            if err:
                print(err)
                return 1
            shown = hr.card(prop)
            print("\n".join(textsafe.drawable(shown["text"])))   # agent-written: nothing a terminal obeys
            if not sys.stdin.isatty():
                print("not a terminal: pass --subject " + shown["subject"], file=sys.stderr)
                return 1
            if input("approve this? [y/N] ").strip().lower() not in ("y", "yes"):
                print("left alone")
                return 1
            subject = shown["subject"]
        return client.said(*_decide(args.id, args.hr_cmd, subject, getattr(args, "reason", "")))
    if args.hr_cmd == "diff":
        if not me:
            print(_who_refusal("propos"))
            return 1
        changes, err = _read_changes(args.file)
        if err:
            print(err, file=sys.stderr)
            return 1
        if me == askmod.HUMAN:
            ok, said = hr.enact_bundle(changes, args.why)
        else:
            ok, said = hr.propose_bundle(changes, me, args.why)
        print(said)
        return 0 if ok else 1
    if args.hr_cmd == "withdraw":
        if not me:
            print(_who_refusal("withdraw"))
            return 1
        if _number(args.id) is None:
            print(f"no proposal {args.id}")
            return 1
        return client.said(*client.write("hr.withdraw", {"proposal": _number(args.id)}))
    if not me:
        print(_who_refusal("propos"))
        return 1
    if args.hr_cmd == "team":
        repos: dict[str, str] = {}
        members = []
        for m in args.member:
            n, _, r = m.partition("=")
            members.append(n)
            if r:
                repos[n if n.startswith(f"{args.name}.") else f"{args.name}.{n}"] = r
        if args.repo:
            repos[args.name] = args.repo
        kw = dict(why=args.why, repo=args.repo, model=args.model, effort=args.effort,
                  vendor=args.vendor, members=members, repos=repos)
        if me == askmod.HUMAN:
            ok, said = hr.enact("team", args.name, **kw)
        else:
            ok, said = hr.propose("team", args.name, requester=me, **kw)
        print(said)
        return 0 if ok else 1
    if args.hr_cmd == "context":
        texts = {}
        for fn, path in (("ROLE.md", args.role_file), ("base.md", args.base_file)):
            if path:
                try:
                    texts[fn] = Path(path).expanduser().read_text()
                except (OSError, UnicodeDecodeError) as e:
                    print(f"{path}: {e}", file=sys.stderr)
                    return 1
        kw = dict(why=args.why, sets=texts)
        if me == askmod.HUMAN:
            ok, said = hr.enact("context", args.name, **kw)
        else:
            ok, said = hr.propose("context", args.name, requester=me, **kw)
        print(said)
        return 0 if ok else 1
    kw = dict(why=args.why, team_name=args.team,
              repo=args.repo, model=args.model, to=getattr(args, "to", None),
              effort=args.effort, vendor=getattr(args, "vendor", None),
              response=getattr(args, "response", None),
              relation=getattr(args, "relation", None))
    # The founder is the approval, so their own change is made, not proposed.
    if me == askmod.HUMAN:
        ok, said = hr.enact(args.hr_cmd, args.name, **kw)
    else:
        ok, said = hr.propose(args.hr_cmd, args.name, requester=me, **kw)
    print(said)
    return 0 if ok else 1


def cmd_trust(args) -> int:
    """Declare, list or drop a root ocp may answer claude's trust prompt in.

    Adding goes through the same propose/approve gate as the roster; the
    founder may also add directly. Removing needs no gate: it only narrows
    what standing consent covers.
    """
    if args.trust_cmd == "list":
        rows = trust.list_roots()
        if not rows:
            print("no declared roots")
            return 0
        for r in rows:
            print(f"{r['root']}  (added by {r.get('added_by', '?')}: {r.get('why', '')})")
        return 0
    if args.trust_cmd == "proposals":
        rows = trust.listing()
        if not rows:
            print("no proposals")
            return 0
        for p_ in rows:
            print(f"{p_['id']}  {p_.get('state', '?'):<10}#{p_.get('request', '-')}  "
                  f"{p_['root']}  by {p_.get('requested_by', '?')}")
        return 0
    if args.trust_cmd == "mcp":
        ok, said = trust.approve_mcp(args.root)
        print(said)
        return 0 if ok else 1
    if args.trust_cmd == "apply":
        ok, said = trust.apply(args.id)
        print(said)
        return 0 if ok else 1
    if args.trust_cmd == "remove":
        ok, said = trust.remove(args.root)
        print(said)
        return 0 if ok else 1
    me = askmod.asking_agent()
    if not me:
        print(_who_refusal("propos"))
        return 1
    if me == askmod.HUMAN:
        ok, said = trust.enact(args.root, args.why or "added by the founder")
    else:
        if not args.why:
            print("say why: the founder is deciding, not rubber-stamping", file=sys.stderr)
            return 1
        ok, said = trust.propose(args.root, args.why, requester=me)
    print(said)
    return 0 if ok else 1
