"""`ocp say` (T293): a message that needs no answer and starts no work - see
`team.ask.say`; work is a task."""
from __future__ import annotations

import re

from .ask import _send, _text_arg

# What a shell leaves when it ran `...` or $(...) inside double quotes: a gap where the output was,
# so two spaces meet between words. Everything it did not run (`()`, `$(x)`) arrives whole.
_LOST = re.compile(r"(?<=\w)  +(?=\w)")


def quoting_warning(body: str) -> str | None:
    """A warning when the body looks like the shell ate part of it; None when it reads whole."""
    if _LOST.search(body):
        return ("warning: the body has a gap that may be text lost to shell quoting (backticks or $( ) "
                "inside double quotes run as commands). Resend with single quotes, or `--file -` and a "
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
