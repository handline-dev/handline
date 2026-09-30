"""`ocp say` (T293): a message that needs no answer and starts no work - see
`team.ask.say`; work is a task."""
from __future__ import annotations

from .ask import _send, _text_arg


def cmd_say(args) -> int:
    if args.target is None:
        print("say needs a target and a body")
        return 1
    body, err = _text_arg(args.body, args.file, "body")
    if err:
        print(err)
        return 1
    return _send("say", target=args.target, body=body, parent=args.parent,
                 task=args.task, fyi=args.fyi, no_task=args.no_task)
