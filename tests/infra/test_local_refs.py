"""Tests: tools/localrefs.py refuses personal data and ids that only mean something on one machine,
in a diff, in a commit message, in the git hooks and in tools/land.sh.

Run on its own: tools/test.sh tests/infra/test_local_refs.py   (all areas: tools/suite.sh)
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

TOOLS = ROOT / "tools"
_spec = importlib.util.spec_from_file_location("localrefs", TOOLS / "localrefs.py")
lr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lr)

# The login a test runs under must not look like a name in a clean text.
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
           GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null",
           USER="nobody", LOGNAME="nobody")


def kinds(text: str, words: tuple[str, ...] = ()) -> list[str]:
    return [k for k, _ in lr.find(text, words)]


def test_each_pattern_is_caught() -> None:
    cases = {
        "email": "mail me at jo.smith" + "@" + "gmail.com",
        "home path": "see /Users/" + "jo/notes/a.md",
        "task id": "fixed in T" + "123",
        "message number": "as asked in #" + "9684",
        "agent name": "ask orcapipe." + "auditor",
        "token": "key sk-ant-" + "oat01-abcdefghijkl",
    }
    for kind, text in cases.items():
        check(f"localrefs: {kind} is caught", kind in kinds(text), str(lr.find(text)))
    check("localrefs: a Linux home path and a Windows one are caught",
          "home path" in kinds("/home/" + "jo/x") and "home path" in kinds("C:\\Users\\" + "jo\\x"))
    check("localrefs: a task id in a branch name is caught", "task id" in kinds("Merge " + "jo/t" + "949-stale-notices"))
    check("localrefs: an id next to Hangul is still one", "task id" in kinds("T" + "835를"))
    check("localrefs: the machine's own name is caught as a whole word, not inside another word",
          kinds("by jo today", ("jo",)) == ["personal name"] and kinds("a major change", ("jo",)) == [])
    # The git user name can be a word the product itself uses (it is this repo's own identity).
    keep = {k: os.environ.get(k) for k in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0")}
    os.environ.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="user.name", GIT_CONFIG_VALUE_0="Handline")
    try:
        check("localrefs: a git user name that is a word of the product's text does not count as personal",
              "Handline" not in lr.own_words()
              and kinds("read both the Handline and ocp names", lr.own_words()) == [], str(lr.own_words()))
    finally:
        for k, v in keep.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def test_clean_text_and_look_alikes_pass() -> None:
    clean = [
        "Add a column to requests and carry only rows marked as notices.",
        "Bump to v1.2.3 and 2.1.273; build 1.4.203",
        "commit a5d23d95 and 72209b6353fa960f831353f1fb0b65122ef5d1b2",
        "at 2026-10-02T10:50:38 the daemon woke",
        "colour #00ff00 and #123456 and #ff8800aa",
        "entity &#1234; and the T-shirt, T1 and a t12 variable",
        "see claude-code#75408 and orca#11557",
        "[the issue #11557](https://github.com/stablyai/orca/issues/11557)",
        "write to user@example.com or <user>@example.org",
        "Co-Authored-By: Claude <noreply@anthropic.com>",
        "git clone git@github.com:org/repo.git",
        "pnpm@10.33.0 and turbo@2.11.7 and a lock line \"@types/node@22.1.0\"",
        "  next@15.0.0-canary.abc: and '@scope/pkg@1.0.0-beta.next(react@19.0.0-rc.abc)':",
        "a path /Users/<name>/x and /Users/you/x and /home/runner/work",
        "the orcapipe project, ocp, orcapipe.yaml and dev.orcapipe.backup",
        "pytest.mark.parametrize and @property and 5 > 3",
        "step # 12 and item #1 and #12",
        "a line with a fixture T" + "999 and local-ref-ok on it",
    ]
    for text in clean:
        check(f"localrefs: clean text passes: {text[:50]}", kinds(text) == [], str(lr.find(text)))


def _diff(path: str, *added: str) -> str:
    body = "\n".join("+" + a for a in added)
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -0,0 +10,{len(added)} @@\n{body}\n"


def test_a_diff_is_read_for_its_added_lines_only() -> None:
    bad = "# was T" + "123"
    out = lr.scan_diff(_diff("a.py", "x = 1", bad), [])
    check("localrefs: an added line is found with its path and line number",
          out == ["a.py:11: task id: T" + "123"], str(out))
    removed = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,0 @@\n-" + bad + "\n"
    check("localrefs: a removed line is not read", lr.scan_diff(removed, []) == [])
    check("localrefs: a deleted file is not read",
          lr.scan_diff("--- a/a.py\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-" + bad + "\n", []) == [])
    check("localrefs: an allowlisted path is skipped", lr.scan_diff(_diff("tests/f.py", bad), ["tests/f.py"]) == [])
    check("localrefs: a glob in the allowlist matches", lr.scan_diff(_diff("tests/x/f.py", bad), ["tests/x/*.py"]) == [])
    check("localrefs: the marker skips one line and only that line",
          len(lr.scan_diff(_diff("a.py", bad + "  # local-ref-ok", bad), [])) == 1)
    lock = lr.scan_diff(_diff("pnpm-lock.yaml", "  '@types/node@20.1.0':", "      vite: 6.3.5(@types/node@20.1.0)",
                                      "  next@15.0.0-canary.abc:", "    author: jo" + "@" + "gmail.com"), [])
    check("localrefs: lockfile package pins are not emails, a real address in a lockfile is",
          lock == ["pnpm-lock.yaml:13: email: jo" + "@" + "gmail.com"], str(lock))
    check("localrefs: a mail domain that starts with a number is still one",
          kinds("write to jo" + "@" + "163.com") == ["email"])
    check("localrefs: the allowlist file itself lists the check's own test",
          lr.is_allowed("tests/infra/test_local_refs.py", lr.allowed_paths()))


def test_a_message_skips_git_comment_lines_only_when_asked() -> None:
    text = "Add a thing\n\nBecause T" + "123 said so\n# on branch x, #" + "4567\n"
    check("localrefs: a message finds the id on its body line and skips git's # lines",
          lr.scan_message(text, "m") == ["m:3: task id: T" + "123"], str(lr.scan_message(text, "m")))
    check("localrefs: a stored commit message is read whole, # lines too",
          len(lr.scan_message(text, "m", comments=False)) == 2)


def _git(cwd: Path, *args: str, check_rc: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=ENV, timeout=30)
    assert not check_rc or r.returncode == 0, f"git {' '.join(args)}: {r.stderr}"
    return r


def _repo(td: Path) -> Path:
    """A main checkout holding the check, its allowlist, the hooks and a green suite."""
    main = td / "main"
    (main / "tools").mkdir(parents=True)
    _git(main, "init", "-q", "-b", "main")
    for name in ("localrefs.py", "localrefs.allow", "land.sh"):
        shutil.copy(TOOLS / name, main / "tools" / name)
    shutil.copytree(TOOLS / "githooks", main / "tools" / "githooks")
    suite = main / "tools" / "suite.sh"
    suite.write_text("#!/usr/bin/env bash\ntrue\n")
    suite.chmod(0o755)
    (main / "a.py").write_text("x = 1\n")
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "base")
    return main


def test_the_git_hooks_refuse_a_dirty_message_and_a_dirty_diff(home: Path) -> None:
    main = _repo(home)
    _git(main, "config", "core.hooksPath", "tools/githooks")
    (main / "b.py").write_text("y = 2\n")
    _git(main, "add", "b.py")
    r = _git(main, "commit", "-m", "Fix it, T" + "123", check_rc=False)
    check("hooks: commit-msg refuses a message with a task id and names it",
          r.returncode != 0 and "task id" in r.stderr, r.stderr)
    r = _git(main, "commit", "-m", "Say why in words\n\nCo-Authored-By: C <noreply@anthropic.com>", check_rc=False)
    check("hooks: a clean message, trailer included, commits", r.returncode == 0, r.stderr)
    (main / "c.py").write_text("# jo@" + "gmail.com\n")
    _git(main, "add", "c.py")
    r = _git(main, "commit", "-m", "Add c", check_rc=False)
    check("hooks: pre-commit refuses a staged email and says where",
          r.returncode != 0 and "c.py:1: email" in r.stderr, r.stderr)
    (main / "c.py").write_text("# jo@" + "gmail.com  local-ref-ok\n")
    _git(main, "add", "c.py")
    check("hooks: the marker lets a fixture through", _git(main, "commit", "-m", "Add c", check_rc=False).returncode == 0)


def _branch(main: Path, name: str, text: str, message: str) -> str:
    _git(main, "checkout", "-q", "-b", name, "main")
    (main / (name + ".py")).write_text(text)
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", message)
    sha = _git(main, "rev-parse", "HEAD").stdout.strip()
    _git(main, "checkout", "-q", "main")
    return sha


def _land(cwd: Path, home: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(ENV, OCP_HOME=str(home / "ocphome"))
    (home / "ocphome").mkdir(exist_ok=True)
    return subprocess.run([str(cwd / "tools" / "land.sh"), *args], cwd=cwd, capture_output=True, text=True,
                          env=env, timeout=60)


def test_land_refuses_dirty_work_and_keeps_the_ids_out_of_git(home: Path) -> None:
    main = _repo(home)
    head = _git(main, "rev-parse", "HEAD").stdout.strip()
    dirty = _branch(main, "dirty", "# see T" + "123\n", "Add dirty")
    r = _land(main, home, dirty, "Merge a clean thing")
    check("land: a branch whose diff adds a task id lands nothing and says so",
          r.returncode == 1 and "task id" in r.stderr and "nothing landed" in r.stderr
          and _git(main, "rev-parse", "HEAD").stdout.strip() == head, r.stderr)
    badmsg = _branch(main, "badmsg", "z = 1\n", "Add z, T" + "123")
    r = _land(main, home, badmsg, "Merge a clean thing")
    check("land: a branch with a commit message carrying an id lands nothing",
          r.returncode == 1 and "task id" in r.stderr and _git(main, "rev-parse", "HEAD").stdout.strip() == head, r.stderr)
    clean = _branch(main, "clean", "w = 1\n", "Add w")
    r = _land(main, home, clean, "Merge task T" + "123, auditor PASS #" + "9549")
    check("land: a merge message with ids is refused too",
          r.returncode == 1 and "task id" in r.stderr and "message number" in r.stderr, r.stderr)
    note = "task T" + "123, PASS #" + "9549"
    r = _land(main, home, "--note", note, clean, "Merge a clean thing")
    merged = r.stdout.strip()
    log = (home / "ocphome" / "landings.log").read_text()
    check("land: a clean branch lands, and --note goes to the local log with the merge sha, not into git",
          r.returncode == 0 and merged in log and note in log
          and "T" + "123" not in _git(main, "log", "-3", "--format=%B").stdout, r.stderr + log)


def test_the_products_own_ocp_syntax_in_a_doc_is_not_a_local_id() -> None:
    ex = "ocp task done T" + "123 <sha>"
    md = lambda *lines: lr.scan_diff(_diff("docs/guide.md", *lines), [])        # noqa: E731
    check("localrefs: an id that is an argument of an ocp command in a Markdown file passes",
          md("Close it with `" + ex + "`", "    " + ex, "ocp read " + "#" + "1234") == [])
    check("localrefs: the same line in code is still a local id", lr.scan_diff(_diff("a.py", "# " + ex), []) != [])
    check("localrefs: an id in prose of a Markdown file is a local id", len(md("this was fixed in T" + "123")) == 1)
    check("localrefs: so is one that only comes near an ocp command, after a sentence end or a comma",
          len(md("ocp ran. See T" + "123")) == 1 and md("ocp task done T" + "123, then T" + "777")
          == ["docs/guide.md:10: task id: T" + "777"])
    check("localrefs: and a message number in a commit message is never an example",
          lr.scan_message("Fix: " + ex, "m") != [])


def test_a_github_link_excuses_only_its_own_number() -> None:
    n = "#" + "9369"
    check("localrefs: a bare number beside a github URL is still ours",
          kinds("see https://github.com/o/r/issues/9369 and " + n) == ["message number"])
    check("localrefs: the number in a markdown link into github is theirs",
          kinds("[" + n + " the issue](https://github.com/o/r/issues/9369)") == [])
    check("localrefs: a fragment inside any URL is not a message number", kinds("https://example.org/a" + n) == [])
    check("localrefs: and a number before a github link, outside it, is ours",
          kinds(n + " [docs](https://github.com/o/r)") == ["message number"])


def test_a_css_hex_colour_is_not_a_message_number() -> None:
    css = lambda *lines: lr.scan_diff(_diff("web/style.css", *lines), [])        # noqa: E731
    check("localrefs: #000 and #0003 in a stylesheet are colours, not message numbers",
          css("a { color: #000; box-shadow: 0 0 1px #0003; }", "b { background: #0004 }") == [])
    check("localrefs: so are they in a style attribute, but not beside it",
          lr.scan_diff(_diff("a.html", '<p style="color:#000">'), []) == []
          and len(lr.scan_diff(_diff("a.html", '<p style="color:#000"> see #' + '1234'), [])) == 1)
    check("localrefs: a message number in a .md line still hits, and so does a 5-digit one in a stylesheet",
          len(lr.scan_diff(_diff("a.md", "fixed in #" + "14477"), [])) == 1
          and len(lr.scan_diff(_diff("a.md", "colour #000"), [])) == 1
          and len(css("/* see #" + "14477 */")) == 1)


def test_an_added_line_that_looks_like_a_file_header_is_still_a_line() -> None:
    diff = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,0 +5,3 @@\n"
            "+ok\n++ looks like a header\n+# T" + "123\n"
            "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -2 +2 @@\n-old\n+new T" + "456\n")
    out = lr.scan_diff(diff, [])
    check("localrefs: the lines after a `++ x` are read, under the right path and number",
          out == ["a.py:7: task id: T" + "123", "b.py:2: task id: T" + "456"], str(out))


def _commit_as(main: Path, email: str, message: str = "Say it in words") -> None:
    env = dict(ENV, GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_EMAIL=email)
    (main / "n.txt").write_text(message)
    subprocess.run(["git", "add", "-A"], cwd=main, env=env, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=main, env=env, check=True, capture_output=True)


def test_the_email_a_commit_carries_is_checked_as_well_as_its_message(home: Path) -> None:
    main = _repo(home)
    base = _git(main, "rev-parse", "HEAD").stdout.strip()
    _commit_as(main, "jo.smith" + "@" + "gmail.com")
    r = subprocess.run([sys.executable, str(TOOLS / "localrefs.py"), "log", f"{base}..HEAD"], cwd=main,
                       capture_output=True, text=True, env=ENV)
    check("localrefs: a commit authored with a personal email is refused, author and committer both named",
          r.returncode == 1 and "author email" in r.stderr and "committer email" in r.stderr
          and "user.email" in r.stderr, r.stderr)
    _git(main, "reset", "-q", "--hard", base)
    for ok in ("12345+jo@users.noreply.github.com", "noreply@anthropic.com", "t@example.com"):
        _commit_as(main, ok)
        r = subprocess.run([sys.executable, str(TOOLS / "localrefs.py"), "log", f"{base}..HEAD"], cwd=main,
                           capture_output=True, text=True, env=ENV)
        check(f"localrefs: a noreply or placeholder author ({ok.split('@')[1]}) passes", r.returncode == 0, r.stderr)
        _git(main, "reset", "-q", "--hard", base)

    # The next commit's identity, before it is made: the hook refuses it.
    _git(main, "config", "core.hooksPath", "tools/githooks")
    env = dict(ENV, GIT_AUTHOR_EMAIL="jo.smith" + "@" + "gmail.com", GIT_COMMITTER_EMAIL="jo.smith" + "@" + "gmail.com")
    (main / "m.txt").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=main, env=env, capture_output=True)
    r = subprocess.run(["git", "commit", "-m", "Say it in words"], cwd=main, env=env, capture_output=True, text=True)
    check("hooks: commit-msg refuses a commit whose author email is personal, and says how to fix it",
          r.returncode != 0 and "author email" in r.stderr and "noreply" in r.stderr, r.stderr)
    env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = "12345+jo@users.noreply.github.com"
    r = subprocess.run(["git", "commit", "-m", "Say it in words"], cwd=main, env=env, capture_output=True, text=True)
    check("hooks: the same commit with a noreply address goes through", r.returncode == 0, r.stderr)


def main() -> int:
    def tests(td):
        run(lambda: test_each_pattern_is_caught())
        run(lambda: test_clean_text_and_look_alikes_pass())
        run(lambda: test_a_diff_is_read_for_its_added_lines_only())
        run(lambda: test_a_message_skips_git_comment_lines_only_when_asked())
        run(lambda: test_the_git_hooks_refuse_a_dirty_message_and_a_dirty_diff(Path(td) / "h1"))
        run(lambda: test_land_refuses_dirty_work_and_keeps_the_ids_out_of_git(Path(td) / "h2"))
        run(lambda: test_the_products_own_ocp_syntax_in_a_doc_is_not_a_local_id())
        run(lambda: test_a_github_link_excuses_only_its_own_number())
        run(lambda: test_an_added_line_that_looks_like_a_file_header_is_still_a_line())
        run(lambda: test_a_css_hex_colour_is_not_a_message_number())
        run(lambda: test_the_email_a_commit_carries_is_checked_as_well_as_its_message(Path(td) / "h3"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
