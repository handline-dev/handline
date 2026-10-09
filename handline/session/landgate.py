"""The session's own `hl land` command, vouched for to the daemon before it runs.

Branch code (the auditor's test run, the merger's `land.sh --check`) runs as a child of the same session
that sends `land_pass` and `land`, so the daemon cannot tell the two apart by who is calling. What a
branch's code cannot do is be the command the session's own PreToolUse hook sees. The hook is run by the
harness outside the Bash sandbox, so it alone can read the per-session key `hl launch` keeps in a folder
the sandbox hides (`infra/sessiontoken.py`). When the tool call is exactly `hl land --pass <sha>` or
`hl land <sha> <message> --tree <tree>` (optionally with `--fix <dir>` and `--squash`) and nothing else, it hands the daemon the key with the verb and
the sha, and the daemon accepts that one request from that session for a minute (`daemon/landauth.py`).
A land verb that nothing vouched for, such as one sent from inside a test run, is refused.
"""
from __future__ import annotations

import shlex

from ..infra import names, sessiontoken

_OPERATORS = set(";&|<>()")


def parse(tool_name: object, tool_input: object) -> tuple[str, str] | None:
    """(verb, sha) when this Bash call is nothing but one `hl land` that sends a verb, else None.
    A command with any operator, substitution or second command in it vouches for nothing."""
    if tool_name != "Bash" or not isinstance(tool_input, dict):
        return None
    cmd = tool_input.get("command")
    if not isinstance(cmd, str) or any(c in cmd for c in "`$\n\r"):
        return None
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = ""                 # a `#` inside a word is part of the word; the shell agrees
        words = list(lex)
    except ValueError:
        return None
    if len(words) < 3 or words[0].rsplit("/", 1)[-1] not in names.COMMANDS or words[1] != "land":
        return None
    if any(set(w) <= _OPERATORS or w.startswith("#") for w in words):
        return None                         # an operator, or a comment the shell would drop
    rest, positional, tree, housekeeping, i = words[2:], [], False, False, 0
    while i < len(rest):
        w = rest[i]
        if w == "--tree":
            tree, i = True, i + 2
        elif w == "--fix":                  # the merger's housekeeping directory: a land's, never a pass's
            housekeeping, i = True, i + 2
        elif w == "--squash":
            housekeeping, i = True, i + 1
        elif w.startswith("-") and w != "--pass":
            return None                     # --check, --list and anything unknown send no verb
        else:
            if w != "--pass":
                positional.append(w)
            i += 1
    if "--pass" in rest:
        return ("land_pass", positional[0]) if len(positional) == 1 and not housekeeping else None
    return ("land", positional[0]) if tree and len(positional) == 2 else None


def authorize(agent: str, tool_name: object, tool_input: object) -> bool:
    """Vouch for this tool call to the daemon if it is a land command. True when the daemon took it."""
    asked = parse(tool_name, tool_input)
    key = sessiontoken.key_held(agent)
    if asked is None or key is None:
        return False
    from ..daemon import client as dmn_client
    reply = dmn_client.send({"verb": "land_authorize", "agent": agent, "land_verb": asked[0], "sha": asked[1],
                             "key": key}, timeout=5)
    return bool(reply and reply.get("ok"))
