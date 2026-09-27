"""`ocp say` (T293): a question, never new work - see `team.ask.say` and
`actions.say` for the shape rules and the wording."""
from __future__ import annotations

from .. import actions
from ..team import ask as askmod
from .. import db

from .ask import _text_arg, _who_refusal


def cmd_say(args) -> int:
    if args.needs_task is not None:
        if args.target is not None or args.body is not None:
            print("--needs-task flags a say you received - it takes no target or body "
                  "of its own; send those as a separate `ocp say`")
            return 1
        return _cmd_say_needs_task(args)
    if args.target is None:
        print("say needs a target and a body, or --needs-task ID to flag one you received")
        return 1
    body, err = _text_arg(args.body, args.file, "body")
    if err:
        print(err)
        return 1
    ok, msg, rid = askmod.say(args.target, body, requester=args.of, parent_id=args.parent)
    print(msg)
    if ok and rid is not None:
        print(actions.delivery_line(rid))
    return 0 if ok else 1


def _cmd_say_needs_task(args) -> int:
    """#1657: only the receiver of a say may decide it was really work - the
    reply carries a ready `task new` command back to whoever sent it."""
    me = args.of or askmod.asking_agent()
    if me is None:
        print(_who_refusal("flag this say"))
        return 1
    con = db.connect()
    try:
        row = db.get_request(con, args.needs_task)
    finally:
        con.close()
    why = actions.needs_task_refusal(row, me)
    if why:
        print(why)
        return 1
    command = actions.needs_task_command(me, row["body"])
    ok, msg, rid = askmod.say(row["requester"], actions.flag_notice(row["id"], command),
                              requester=me, parent_id=row["id"])
    print(msg)
    return 0 if ok else 1
