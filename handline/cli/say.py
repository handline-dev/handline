"""`ocp say` (T293): a message that needs no answer and starts no work - see
`team.ask.say`; work is a task."""
from __future__ import annotations

import re

from .ask import _send, _text_arg

# What a shell leaves behind when it ran `...` or $(...) inside double quotes: an empty span, or a
# gap where the output was (nothing, so two spaces meet).
_LOST = re.compile(r"``|\$\(|\(\)|''|\"\"|(?<=\w)  +(?=\w)")


def quoting_warning(body: str) -> str | None:
    """A warning when the body looks like the shell ate part of it; None when it reads whole."""
    if _LOST.search(body) or body.count("`") % 2:
        return ("warning: the body looks like it lost text to shell quoting (backticks or $( ) inside "
                "double quotes run as commands). Resend with single quotes, or `--file -` and a "
                "heredoc, if text is missing.")
    return None


def cmd_say(args) -> int:
    if args.target is None:
        print("say needs a target and a body")
        return 1
    body, err = _text_arg(args.body, args.file, "body")
    if err:
        print(err)
        return 1
    warn = quoting_warning(body) if args.file is None and args.body != "-" else None
    if warn:
        print(warn)
    return _send("say", target=args.target, body=body, parent=args.parent,
                 task=args.task, no_task=args.no_task)
