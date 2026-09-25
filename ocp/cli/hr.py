"""`ocp hr` and `ocp trust`."""
from __future__ import annotations

import sys

from ..team import ask as askmod
from .. import db
from ..team import hr
from ..team import rehome
from ..team import trust

from .ask import _who_refusal
from .health import _name_col


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
        skipped: list[str] = []
        rows = hr.listing(skipped)
        for why in skipped:
            print(f"skipped, cannot be read - {why}", file=sys.stderr)
        if not rows:
            print("no proposals")
            return 0
        for p_ in rows:
            print(f"{p_['id']}  {p_['kind']:<7}{p_['agent']:<{_name_col([r['agent'] for r in rows])}}"
                  f"{p_.get('state', '?'):<10}"
                  f"#{p_.get('request', '-')}  by {p_.get('requested_by', '?')}")
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
    if args.hr_cmd == "apply":
        ok, said = hr.apply(args.id)
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
