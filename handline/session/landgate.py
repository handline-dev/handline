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
MISSES = ("Refused before it ran: `hl land` sends its verb only when it is the whole Bash call, so this one would be turned "
          "away. Type it alone on one line: no cd or && before it, no 2>&1, pipe, ; or # after it, and no $ or backtick "
          "anywhere, quoted or not. `hl land --pass <sha>`, or `hl land <sha> '<message>' --tree <tree>`.")


def parse(tool_name: object, tool_input: object) -> tuple[str, str] | None:
    """(verb, sha) when this Bash call is nothing but one `hl land` that sends a verb, else None.
    A command with any operator, substitution or second command in it vouches for nothing."""
    if tool_name != "Bash" or not isinstance(tool_input, dict):
        return None
    cmd = tool_input.get("command")
    if not isinstance(cmd, str) or any(c in cmd for c in "`$\n\r"):
        return None
    words = _words(cmd)                     # a `#` inside a word is part of the word; the shell agrees
    if words is None:
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


def misses(agent: str, tool_name: object, tool_input: object) -> str | None:
    """Why this Bash call would send a land verb nothing vouches for: it holds an `hl land` with more around it. None when it
    would be vouched for, sends no verb, or the session has no key to vouch with (no retyping would help that)."""
    cmd = tool_input.get("command") if tool_name == "Bash" and isinstance(tool_input, dict) else None
    words = _words(cmd) if isinstance(cmd, str) else None
    if not words or not _sends_land(words) or parse(tool_name, tool_input) is not None or sessiontoken.key_held(agent) is None:
        return None
    return MISSES


def _words(cmd: str) -> list[str] | None:
    """The command split as the shell would (a quoted argument is one word, a run of operators another); None when it cannot be."""
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = ""
        return list(lex)
    except ValueError:
        return None


def _sends_land(words: list[str]) -> bool:
    """Whether a command in words is `hl land` sending a verb (a --pass or a --tree, no --check or --list): the command word at the
    start or after an operator, so a land named inside a quoted note or a heredoc line is not one."""
    for i, w in enumerate(words[:-1]):
        if w.rsplit("/", 1)[-1] in names.COMMANDS and words[i + 1] == "land" and (i == 0 or set(words[i - 1]) <= _OPERATORS):
            args = []
            for x in words[i + 2:]:
                if set(x) <= _OPERATORS:
                    break
                args.append(x)
            if ("--pass" in args or "--tree" in args) and not {"--check", "--list"} & set(args):
                return True
    return False


def authorize(agent: str, tool_name: object, tool_input: object) -> bool:
    """Vouch for this tool call to the daemon if it is a land command. True when the daemon took it."""
    asked = parse(tool_name, tool_input)
    key = sessiontoken.key_held(agent)
    if asked is None or key is None:
        return False
    from ..infra import daemonclient as dmn_client
    reply = dmn_client.send({"verb": "land_authorize", "agent": agent, "land_verb": asked[0], "sha": asked[1],
                             "key": key}, timeout=5)
    return bool(reply and reply.get("ok"))
