"""`ocp say` (T293): a question, never new work - see `team.ask.say` and
`actions.say` for the shape rules and the wording."""
from __future__ import annotations

from .ask import _send, _text_arg


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
    return _send("say", target=args.target, body=body, parent=args.parent)


def _cmd_say_needs_task(args) -> int:
    """#1657: only the receiver of a say may decide it was really work - the
    reply carries a ready `task new` command back to whoever sent it."""
    return _send("say", needs_task=args.needs_task)
