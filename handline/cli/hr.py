"""`hl hr` and `hl trust`."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from ..team import ask as askmod
from .. import db
from ..team import hr, proposals
from ..team import rehome
from ..team import trust
from ..infra import protected, textsafe

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


def _approve_many(ids: list[str], yes: bool, me: str) -> int:
    """The founder approving several proposals at once: each is read as it stands now, shown, and
    confirmed once; they are applied one after another and each proposer gets one message."""
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
    ok, lines = hr.decide_many(entries, "approved", me)
    print("\n".join(lines))
    return 0 if ok else 1


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
        if args.json:
            print(json.dumps(hr.pending() + (hr.decided() if args.all else []), indent=2))
            return 0
        skipped: list[str] = []
        rows = proposals.listing(skipped)
        for why in skipped:
            print(f"skipped, cannot be read - {why}", file=sys.stderr)
        if not rows:
            print("no proposals")
            return 0
        for p_ in rows:
            print(f"{p_['id']}  {p_['kind']:<7}"
                  f"{textsafe.oneline(str(p_['agent'])):<{_name_col([r['agent'] for r in rows])}}"
                  f"{p_.get('state', '?'):<10}"
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
        ok, said = hr.apply(args.id)
        print(said)
        return 0 if ok else 1
    if args.hr_cmd in ("approve", "reject"):
        if protected.in_agent_session() or me != askmod.HUMAN:
            print("approving or rejecting a proposal is the founder's, from a terminal or the "
                  "screen, not an agent's", file=sys.stderr)
            return 1
        if args.hr_cmd == "approve":
            ids = [c["id"] for c in hr.pending()] if args.all else list(args.ids)
            if args.all and args.ids:
                print("name proposals, or --all, not both", file=sys.stderr)
                return 1
            if not ids:
                print("no proposals" if args.all else "name the proposals to approve, or --all", file=sys.stderr)
                return 1 if not args.all else 0
            if len(ids) > 1 or args.all:
                return _approve_many(ids, args.yes, me)
            args.id = ids[0]
        elif args.all:
            # A reject changes nothing, and its proposer may raise it again, so it is not asked twice.
            if args.id:
                print("name a proposal, or --all, not both", file=sys.stderr)
                return 1
            ids = [c["id"] for c in hr.pending()]
            if not ids:
                print("no proposals")
                return 0
            ok, lines = hr.decide_many([(pid, "") for pid in ids], "rejected", me, args.all_reason)
            print("\n".join(lines))
            return 0 if ok else 1
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
        ok, said = hr.decide(args.id, "approved" if args.hr_cmd == "approve" else "rejected",
                             me, getattr(args, "reason", ""), subject)
        print(said)
        return 0 if ok else 1
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
        ok, said = hr.withdraw(args.id, me)
        print(said)
        return 0 if ok else 1
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
