#!/usr/bin/env python3
"""Refuse text that carries personal data or ids that only mean something on one machine.

  tools/localrefs.py diff [--staged | RANGE]   the lines a diff adds (RANGE is a git revision range)
  tools/localrefs.py log RANGE                 the commit messages in RANGE
  tools/localrefs.py message FILE              one commit message, as the commit-msg hook gets it
  tools/localrefs.py ident                     the author and committer email a commit made now would carry

Exit 0: clean. Exit 1: findings, one per line as `where: kind: text`, then what to write instead.
A path in tools/localrefs.allow is skipped whole; a line holding `local-ref-ok` is skipped.
Only added lines are read, so old text never blocks a new change. `log` reads each commit's author
and committer email as well as its message, so a personal address cannot ride in the metadata.
In a Markdown file, an id that is an argument of an `ocp ...` command on its line is the product's
own syntax, not a local id, and passes.
"""
from __future__ import annotations

import fnmatch
import getpass
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ALLOW_FILE = HERE / "localrefs.allow"
MARKER = "local-ref-ok"

# ASCII word edges, so an id followed by a Korean particle still counts but one inside a hex
# sha or an ISO time (2026-01-01T10:00) never does.
_L, _R = r"(?<![A-Za-z0-9_])", r"(?![A-Za-z0-9_])"

PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")),
    ("home path", re.compile(r"(?:/Users|/home)/([A-Za-z0-9._-]+)|[A-Za-z]:\\Users\\([A-Za-z0-9._-]+)")),
    ("task id", re.compile(_L + r"(?:T\d{2,}|t\d{3,})" + _R)),
    # 3-5 digits and a hard edge: a 6-digit colour and a hex run never match; a repo#123 or &#123; is not ours
    ("message number", re.compile(r"(?<![A-Za-z0-9_&#/-])#\d{3,5}" + _R)),
    ("agent name", re.compile(r"(?<![\w./-])orcapipe\.(?!(?:py|ya?ml|json|db|log|sock|md|toml|plist|lock|sqlite|txt|sh|backup)\b)[a-z][a-z0-9_]*")),
    ("token", re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}"
                         r"|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}"
                         r"|-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]

# A mail domain or a user directory name that is a placeholder, not a person.
SAFE_EMAIL_DOMAINS = {"example.com", "example.org", "example.net"}
SAFE_EMAIL_LOCALS = {"noreply", "no-reply", "git"}
SAFE_USERS = {"user", "you", "me", "name", "username", "runner", "shared", "guest", "example"}
GENERIC_LOGINS = {"root", "admin", "user", "dev", "runner", "ubuntu", "ec2-user", "git", "nobody"}

HINT = ("say what changed and why in words; task and chat ids stay on the task board, and "
        "tools/land.sh --note records them locally. An email, name or home path goes as a "
        "placeholder (<user>, user@example.com). A personal author or committer email: "
        "`git config user.email <id>+<name>@users.noreply.github.com`. Real need (a fixture): "
        "add `local-ref-ok` to the line, or the file to tools/localrefs.allow.")


# The text of a link into someone else's tracker, and any URL: a number in either is theirs, not ours.
_EXTERNAL = re.compile(r"\[[^\]]*\]\(https?://github\.com/[^)\s]*\)|https?://[^\s)>\]]+")
_OCP_COMMAND = re.compile(r"\bocp\s+[a-z][\w-]*")


def _inside(spans: list[tuple[int, int]], pos: int) -> bool:
    return any(a <= pos < b for a, b in spans)


def _in_ocp_command(text: str, pos: int) -> bool:
    """Whether `pos` is an argument of an `ocp <verb> ...` on this line: a few words after the verb and
    no sentence end between, so an id in `ocp task done <id> <sha>` is one and an id after "ocp ran." is not."""
    for c in _OCP_COMMAND.finditer(text):
        if c.start() < pos:
            seg = text[c.end():pos]
            if len(seg.split()) <= 8 and not re.search(r"[;!?]|[.,](?:\s|$)", seg):
                return True
    return False


def find(text: str, words: tuple[str, ...] = (), doc: bool = False) -> list[tuple[str, str]]:
    """(kind, matched text) for everything in one line of text. `words` are this machine's own
    names (login, git user); they count as personal data wherever they stand alone. `doc` is a line
    of a Markdown file, where an id in an `ocp` command is an example of the product's syntax."""
    if MARKER in text:
        return []
    external = [m.span() for m in _EXTERNAL.finditer(text)]
    out: list[tuple[str, str]] = []
    for kind, rx in PATTERNS:
        for m in rx.finditer(text):
            s = m.group(0)
            if kind == "email":
                local, _, domain = s.rpartition("@")
                if (domain.lower() in SAFE_EMAIL_DOMAINS or domain.lower().endswith("noreply.github.com")
                        or local.lower() in SAFE_EMAIL_LOCALS):
                    continue
            elif kind == "home path":
                if (m.group(1) or m.group(2) or "").lower() in SAFE_USERS:
                    continue
            elif kind == "message number" and _inside(external, m.start()):
                continue                  # the number of an issue in someone else's tracker
            if doc and kind in ("task id", "message number") and _in_ocp_command(text, m.start()):
                continue
            out.append((kind, s))
    for w in words:
        if re.search(r"(?<![A-Za-z0-9])" + re.escape(w) + r"(?![A-Za-z0-9])", text, re.IGNORECASE):
            out.append(("personal name", w))
    return out


def own_words() -> tuple[str, ...]:
    """The login and git user name of whoever runs this, minus anything generic."""
    words: set[str] = set()
    try:
        words.add(getpass.getuser())
    except Exception:
        pass
    r = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True)
    if r.returncode == 0:
        words.add(r.stdout.strip())
    return tuple(sorted(w for w in words if len(w) >= 3 and w.lower() not in GENERIC_LOGINS))


def allowed_paths() -> list[str]:
    try:
        lines = ALLOW_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [l.split("#", 1)[0].strip() for l in lines if l.split("#", 1)[0].strip()]


def is_allowed(path: str, allow: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in allow)


def scan_diff(diff: str, allow: list[str], words: tuple[str, ...] = ()) -> list[str]:
    """Findings for the added lines of a `git diff -U0` text. A hunk's lines are counted from its
    header, so an added line that happens to read `++ x` (which a diff shows as `+++ x`) is a line
    of the hunk and not the next file's header."""
    out: list[str] = []
    path, lineno, adds, dels = "", 0, 0, 0
    for raw in diff.splitlines():
        if adds or dels:
            if raw.startswith("+") and adds:
                adds -= 1
                if path and not is_allowed(path, allow):
                    for kind, s in find(raw[1:], words, doc=path.endswith(".md")):
                        out.append(f"{path}:{lineno}: {kind}: {s}")
                lineno += 1
                continue
            if raw.startswith("-") and dels:
                dels -= 1
                continue
        if raw.startswith("+++ "):
            path = raw[6:] if raw.startswith("+++ b/") else ""
        elif raw.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", raw)
            if m:
                dels = int(m.group(1)) if m.group(1) is not None else 1
                lineno = int(m.group(2))
                adds = int(m.group(3)) if m.group(3) is not None else 1
    return out


def scan_message(text: str, where: str, words: tuple[str, ...] = (), comments: bool = True) -> list[str]:
    """Findings for a commit message. With `comments`, a line starting with # is git's own and skipped."""
    out: list[str] = []
    for i, line in enumerate(text.splitlines(), 1):
        if comments and line.startswith("#"):
            continue
        for kind, s in find(line, words):
            out.append(f"{where}:{i}: {kind}: {s}")
    return out


def scan_emails(emails: list[tuple[str, str]], where: str) -> list[str]:
    """Findings for the author/committer emails of one commit, as (role, email) pairs. An email
    is personal unless it is a placeholder or a noreply address."""
    return [f"{where}: {role} email: {email}" for role, email in emails
            if any(kind == "email" for kind, _ in find(email))]


def _git(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"localrefs: git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("diff", "log", "message", "ident"):
        print(__doc__, file=sys.stderr)
        return 2
    mode, rest = argv[0], argv[1:]
    words, allow = own_words(), allowed_paths()
    if mode == "diff":
        spec = ["--cached"] if rest[:1] == ["--staged"] else rest[:1]
        found = scan_diff(_git("diff", "-U0", "--no-color", "--no-ext-diff", *spec), allow, words)
    elif mode == "log":
        found = []
        for chunk in _git("log", "--format=%x01%h%x02%ae%x02%ce%n%B", *rest[:1]).split("\x01")[1:]:
            head, _, body = chunk.partition("\n")
            sha, author, committer = (head.split("\x02") + ["", ""])[:3]
            found += scan_emails([("author", author), ("committer", committer)], f"commit {sha}")
            found += scan_message(body, f"commit {sha}", words, comments=False)
    elif mode == "ident":
        found = []
        for role, var in (("author", "GIT_AUTHOR_IDENT"), ("committer", "GIT_COMMITTER_IDENT")):
            email = re.search(r"<([^>]*)>", _git("var", var))
            found += scan_emails([(role, email.group(1) if email else "")], "next commit")
    else:
        found = scan_message(Path(rest[0]).read_text(encoding="utf-8"), "message", words)
    if found:
        print("\n".join(found), file=sys.stderr)
        print(f"localrefs: {len(found)} hit(s). {HINT}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
