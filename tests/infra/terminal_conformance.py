"""What every terminal host must do: one contract, run against each backend.

A new host is accepted when `conform` passes on it (design/05-terminal-host.md section 4). The
caller hands over a `Harness`: the backend, and the few things only the host's own fake or
real process can do - be killed and revived, and name a handle it never made."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from support import check
from handline.session.backends import base

STATES = {"known", "added", "not_git", "not_root", "no_orca", "failed"}


@dataclass
class Harness:
    backend: base.Backend
    bogus: str                      # a handle of the host's own shape that no terminal has
    down: Callable[[], None]        # the host stops answering
    up: Callable[[], None]          # and answers again
    cwd: str                        # a folder a terminal may start in
    repo: str                       # a git repository with a commit on `main`
    plain: str                      # a folder that is no repository


def _clean(handle) -> bool:
    return isinstance(handle, str) and bool(handle) and handle.isprintable()


def conform(label: str, h: Harness) -> None:
    b = h.backend

    def ck(what: str, ok, detail: str = "") -> None:
        check(f"conformance {label}: {what}", bool(ok), detail)

    ck("it is there and says where it runs", b.available() is True and len(b.own_terminal()) == 2)
    ck("its optional abilities are flags",
       all(isinstance(getattr(b, f), bool) for f in ("supports_runs", "supports_labels")))

    # -- terminals: handles are opaque, unique, listed while alive, gone once closed
    first, why = b.create_terminal("echo one", h.cwd, "conform-1")
    second, _ = b.create_terminal("echo two", h.cwd, "conform-2")
    ck("a terminal is created and named by a clean handle", _clean(first) and why == "", f"{first!r} {why!r}")
    ck("handles are unique and the host knows its own", _clean(second) and second != first
       and b.knows_handle(first) and b.knows_handle(second))
    listed = b.list_terminals()
    ck("both are listed", isinstance(listed, set) and {first, second} <= listed, str(listed))
    screen = b.read_screen(first, 5)
    ck("a screen read is the rendered bottom rows", isinstance(screen, dict) and screen.get("source") == "screen"
       and isinstance(screen.get("tail"), list) and len(screen["tail"]) <= 5
       and all(isinstance(r, str) for r in screen["tail"]), str(screen))
    ck("text, an escape sequence and a bare Enter are all sent",
       b.send(first, "hello") == (True, "") and b.send(first, "\x1b[A", enter=False) == (True, "")
       and b.send(first, "", enter=True) == (True, ""))
    idle = b.wait_idle(first, 1000)
    ck("an idle wait answers a bool and takes a driver's reading of the screen",
       isinstance(idle, bool) and isinstance(b.wait_idle(first, 1000, lambda tail: False), bool))
    ok, _why = b.focus(first)
    ck("focus answers", isinstance(ok, bool))

    # -- a terminal that is not there is an answer, not an exception
    ck("an unknown terminal reads as None, sends nothing and is not idle",
       b.read_screen(h.bogus) is None and b.send(h.bogus, "x")[0] is False
       and b.wait_idle(h.bogus, 300) is False and b.close_terminal(h.bogus)[0] is False)
    ok, why = b.send(h.bogus, "x")
    ck("and a refusal says why", not ok and isinstance(why, str) and why != "")

    # -- closing
    ck("a terminal closes", b.close_terminal(first) [0] is True)
    after = b.list_terminals()
    ck("and is no longer listed", isinstance(after, set) and first not in after and second in after, str(after))
    ok, why = b.close_terminal(first)
    ck("closing it again is refused with words, not raised", ok is False and isinstance(why, str))

    # -- a dead host is None, never an empty set
    h.down()
    try:
        made, why = b.create_terminal("echo x", h.cwd)
        ck("a dead host lists None, not an empty set", b.list_terminals() is None)
        ck("and reads, sends, waits and closes as failures",
           b.read_screen(second) is None and b.send(second, "x")[0] is False
           and b.wait_idle(second, 300) is False and b.close_terminal(second)[0] is False)
        ck("and cannot create, saying why", made is None and isinstance(why, str) and why != "", f"{made!r} {why!r}")
    finally:
        h.up()
    again = b.list_terminals()
    ck("it answers again once back", isinstance(again, set) and second in again, str(again))
    b.close_terminal(second)

    # -- repos and worktrees: git does it where the host does not
    state, said = b.ensure_repo(h.repo, True)
    ck("a repository is known or added", state in ("known", "added") and isinstance(said, str), f"{state} {said}")
    state, said = b.ensure_repo(h.plain, True)
    ck("a folder that is no repository is refused, and not made one", state in ("not_git", "not_root") and said, state)
    path, why = b.create_worktree(h.repo, "conform-wt", "main")
    ck("a worktree is made from a base", isinstance(path, str) and path and why == "", f"{path!r} {why!r}")

    # -- what the host does not keep is said so, and the host words its own fallbacks
    if not b.supports_labels:
        ck("a host without labels answers UNREADABLE and refuses the sets",
           b.worktree_info(h.repo) is base.UNREADABLE and b.set_worktree(h.repo, label="x")[0] is False
           and b.adopt_worktree("k", h.repo)[0] is False and b.terminals_in(h.repo) is None)
    if not b.supports_runs:
        ck("a host without runs makes none", b.run_create("o") is None and b.run_current() is None
           and b.task_create("s", "t", "d", "r") is None and b.dispatch("t", "h", "r") is None)
    words = [b.manual("terminal", worktree="path:/wt", command="hl launch x"),
             b.manual("start", wt="/wt", repo="/r", name="x", base_ref="main", command="hl launch x"),
             b.manual("start", wt=None, repo="/r", name="x", base_ref="main", command="hl launch x"),
             b.manual("label", path="/wt", label="x"), b.manual("parent", child="c", path="/wt")]
    ck("it words each manual fallback", all(isinstance(w, str) and w for w in words), str(words))
