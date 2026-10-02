"""Tests: the terminal boundary. Everything ocp asks of the host that runs an agent's terminal goes through
a Backend; Orca and tmux are two of them, and one config key picks.

Run on its own: tools/test.sh tests/infra/test_terminal_backends.py   (all areas: tools/suite.sh)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403


class FakeHost(backends.base.Backend):
    """A backend that records what it was asked and answers from a dict, to prove who calls what."""
    name = "fake"
    supports_runs = True

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.screen = {"tail": ["> "], "source": "screen"}
        self.idle = True

    def _note(self, *a):
        self.calls.append(a)

    def available(self): return True
    def own_terminal(self): return "h-own", "/wt/own"
    def create_terminal(self, command, cwd=None, title=None):
        self._note("create", command, cwd, title)
        return "h-new", ""
    def close_terminal(self, handle, tab=True):
        self._note("close", handle, tab)
        return True, ""
    def list_terminals(self): return {"h1", "h2"}
    def read_screen(self, handle, limit=40):
        self._note("read", handle, limit)
        return self.screen
    def send(self, handle, text, enter=True, wait_submit_s=None):
        self._note("send", handle, text, enter, wait_submit_s)
        return True, ""
    def wait_idle(self, handle, timeout_ms):
        self._note("idle", handle, timeout_ms)
        return self.idle
    def focus(self, handle):
        self._note("focus", handle)
        return True, ""
    def ensure_repo(self, path, present=True):
        self._note("repo", path, present)
        return "known", "ok"
    def create_worktree(self, repo, name, base_ref):
        self._note("worktree", repo, name, base_ref)
        return "/wt/new", ""
    def run_create(self, objective): return "run-1"
    def run_current(self): return "run-0"
    def run_exists(self, run): return run == "run-1"
    def run_use(self, run): return True
    def task_create(self, spec, title, display_name, run, parent=None): return "task-1"
    def dispatch(self, task, handle, run): return "disp-1"


def test_the_config_key_picks_the_backend_and_a_typo_stops_instead_of_falling_back(home: Path) -> None:
    use_home(home)
    check("backend: Orca is the default", backends.current().name == "orca")
    (home / "config.yaml").write_text("terminal: tmux\n")
    check("backend: `terminal: tmux` in config.yaml picks tmux", backends.current().name == "tmux")
    os.environ["OCP_TERMINAL"] = "orca"
    check("backend: the environment wins over the file", backends.current().name == "orca")
    os.environ["OCP_TERMINAL"] = "tmx"
    odd = backends.current()
    check("backend: a name nobody knows is a backend that is never there and never answers",
          odd.name == "tmx" and not odd.available() and odd.list_terminals() is None
          and odd.read_screen("x") is None and odd.send("x", "y")[0] is False
          and odd.create_terminal("c")[0] is None and "tmx" in odd.ensure_repo("/p")[1])
    os.environ.pop("OCP_TERMINAL")
    pinned = FakeHost()
    before = backends.use(pinned)
    check("backend: a test can pin one, and unpin it", backends.current() is pinned
          and backends.use(before) is pinned)
    (home / "config.yaml").write_text("terminal: orca\n")
    check("backend: the file is read again when it changes", backends.current().name == "orca")


def _record(replies: dict | None = None):
    """Stand in for the Orca CLI's one process-starting function; returns the calls it saw."""
    seen: list[list[str]] = []
    replies = replies or {}

    def fake(argv, timeout=120):
        seen.append(list(argv))
        for prefix, reply in replies.items():
            if argv[1:1 + len(prefix.split())] == prefix.split():
                return reply
        return 0, "", ""
    return seen, fake


def _ok(**result) -> tuple[int, str, str]:
    return 0, json.dumps({"ok": True, "result": result}), ""


def test_the_orca_backend_makes_the_same_cli_calls_it_always_did(home: Path) -> None:
    use_home(home)
    b = orcacli.OrcaBackend()
    real = orcacli._exec
    try:
        seen, orcacli._exec = _record({
            "terminal list": _ok(terminals=[{"handle": "t1"}, {"handle": "t2"}, {"nohandle": 1}]),
            "terminal read": _ok(terminal={"handle": "t1", "tail": ["a", "b"], "source": "screen"}),
            "terminal wait": _ok(wait={"satisfied": True}),
            "terminal create": _ok(terminal={"handle": "t9"}),
        })
        check("orca: list is every handle", b.list_terminals() == {"t1", "t2"}, str(seen))
        check("orca: list asks `terminal list --json`", seen[-1] == ["orca", "terminal", "list", "--json"], str(seen[-1]))
        term = b.read_screen("t1", 12)
        check("orca: a screen read is the terminal dict, 12 rows asked for",
              term and term["tail"] == ["a", "b"]
              and seen[-1] == ["orca", "terminal", "read", "--terminal", "t1", "--screen", "--limit", "12", "--json"],
              str(seen[-1]))
        check("orca: send is --text and --enter, with --wait-submit when asked",
              b.send("t1", "hi") == (True, "")
              and seen[-1] == ["orca", "terminal", "send", "--terminal", "t1", "--text", "hi", "--enter"]
              and b.send("t1", "x", enter=False, wait_submit_s=30)[0]
              and seen[-1] == ["orca", "terminal", "send", "--terminal", "t1", "--text", "x", "--wait-submit", "30"],
              str(seen[-2:]))
        check("orca: idle is the wait's `satisfied`, not its exit code",
              b.wait_idle("t1", 5000) is True
              and seen[-1] == ["orca", "terminal", "wait", "--terminal", "t1", "--for", "tui-idle",
                               "--timeout-ms", "5000", "--json"], str(seen[-1]))
        orcacli._exec = _record({"terminal wait": _ok(wait={"satisfied": False})})[1]
        check("orca: a timed-out wait is not idle even though it ran", b.wait_idle("t1", 10) is False)
        orcacli._exec = _record({"terminal wait": (1, "", "boom")})[1]
        check("orca: and a wait that failed is not idle either", b.wait_idle("t1", 10) is False)

        seen, orcacli._exec = _record({"terminal create": _ok(terminal={"handle": "t9"})})
        check("orca: a terminal in a path is `--worktree path:<dir> --title --command --json`",
              b.create_terminal("ocp launch x", "/wt/a", "x") == ("t9", "")
              and seen[-1] == ["orca", "terminal", "create", "--worktree", "path:/wt/a", "--title", "x",
                               "--command", "ocp launch x", "--json"], str(seen[-1]))
        check("orca: with no path it is Orca's `current` worktree",
              b.create_terminal("c")[0] == "t9" and seen[-1][:5] == ["orca", "terminal", "create", "--worktree", "current"],
              str(seen[-1]))
        orcacli._exec = _record({"terminal create": (1, "", "no room")})[1]
        check("orca: a refused create says why", b.create_terminal("c", "/w") == (None, "no room"))
        orcacli._exec = _record({"terminal create": (1, "", "")})[1]
        check("orca: even a refusal with no words is a reason, never a bare nothing",
              b.create_terminal("c", "/w")[0] is None and b.create_terminal("c", "/w")[1] != "")
        orcacli._exec = _record({"terminal create": _ok(terminal={"handle": "bad\x1b[31mh"})})[1]
        check("orca: a handle with a control byte is not a handle, and not a failure",
              b.create_terminal("c", "/w") == (None, ""))

        seen, orcacli._exec = _record()
        b.close_terminal("t1")
        b.close_terminal("t1", tab=False)
        b.focus("t1")
        check("orca: close takes the tab unless told not to, and focus is `terminal switch`",
              seen[0] == ["orca", "terminal", "close", "--terminal", "t1", "--tab"]
              and seen[1] == ["orca", "terminal", "close", "--terminal", "t1"]
              and seen[2] == ["orca", "terminal", "switch", "--terminal", "t1"], str(seen))

        check("orca: it keeps runs and labels, and has its own CLI for them",
              b.supports_runs and b.supports_labels and b.host_cli(["worktree", "show"])[0]
              and seen[-1] == ["orca", "worktree", "show"])
    finally:
        orcacli._exec = real


def test_the_orca_backend_makes_worktrees_and_registers_repos_as_before(home: Path) -> None:
    use_home(home)
    repo = home / "repo"
    (repo / ".git").mkdir(parents=True)
    b = orcacli.OrcaBackend()
    real = orcacli._exec
    try:
        made = json.dumps({"ok": True, "result": {"worktree": {"path": "/wt/new"}}})
        seen, orcacli._exec = _record({"worktree create": (0, made, "")})
        check("orca: a worktree is `worktree create --repo path:<repo> --name --base-branch --json`",
              b.create_worktree(str(repo), "x", "origin/main") == ("/wt/new", "")
              and seen[-1] == ["orca", "worktree", "create", "--repo", f"path:{repo}", "--name", "x",
                               "--base-branch", "origin/main", "--json"], str(seen[-1]))
        orcacli._exec = _record({"worktree create": (0, json.dumps({"result": {"worktree": {"path": "a\nb"}}}), "")})[1]
        path, why = b.create_worktree(str(repo), "x", "main")
        check("orca: a path with a newline is refused where it is read", path is None
              and why.startswith("could not read the new worktree path"), why)
        orcacli._exec = _record({"worktree create": (1, "", "disk full")})[1]
        check("orca: a refusal is named", b.create_worktree(str(repo), "x", "main")
              == (None, "worktree create failed: disk full"))

        # repo_not_found: add the repo (never twice), then try again once.
        state = {"added": False}
        not_found = json.dumps({"ok": False, "error": {"code": "repo_not_found"}})

        def fake(argv, timeout=120):
            if argv[1:3] == ["worktree", "create"]:
                return (0, made, "") if state["added"] else (1, not_found, "")
            if argv[1:3] == ["repo", "list"]:
                return 0, json.dumps({"ok": True, "result": {"repos": []}}), ""
            if argv[1:3] == ["repo", "add"]:
                state["added"] = True
                return 0, json.dumps({"ok": True}), ""
            return 0, "", ""
        orcacli._exec = fake
        check("orca: repo_not_found registers the repo and the second try works",
              b.create_worktree(str(repo), "x", "main") == ("/wt/new", "") and state["added"])
        check("orca: no host at all is `no_orca` and says what to run",
              b.ensure_repo(str(repo), present=False)[0] == "no_orca")
        orcacli._exec = lambda argv, timeout=120: (0, json.dumps({"ok": True, "result": {"repos": [{"path": str(repo)}]}}), "")
        check("orca: a repo it already lists is `known`", b.ensure_repo(str(repo))[0] == "known")
        plain = home / "plain"
        plain.mkdir()
        orcacli._exec = lambda argv, timeout=120: (0, json.dumps({"ok": True, "result": {"repos": []}}), "")
        check("orca: a folder without .git is `not_git` and is not added", b.ensure_repo(str(plain))[0] == "not_git")
    finally:
        orcacli._exec = real


def test_the_terminal_layer_reaches_the_host_only_through_the_backend(home: Path) -> None:
    use_home(home)
    for name, real in REAL_ORCA.items():        # the test world stubs some of these; this test is about the real ones
        setattr(orcamod, name, real)
    host = FakeHost()
    backends.use(host)
    check("layer: available, handles and live terminals come from the backend",
          orcamod.available() and orcamod.terminal_handle() == "h-own" and orcamod.worktree_id() == "/wt/own"
          and orcamod.live_terminals() == {"h1", "h2"} and orcamod.alive("h1") and not orcamod.alive("h3"))
    check("layer: a new terminal's handle, and with the reason",
          orcamod.create_terminal("cmd", cwd="/w", title="t") == "h-new"
          and orcamod.open_terminal("cmd", cwd="/w", title="t") == ("h-new", "")
          and host.calls[-1] == ("create", "cmd", "/w", "t"))
    ok, _ = orcamod.send("h1", "hello")
    ok2, _ = orcamod.submit("h1", "task", 30)
    check("layer: send and submit are the backend's send",
          ok and ok2 and ("send", "h1", "hello", True, None) in host.calls
          and ("send", "h1", "task", True, 30) in host.calls, str(host.calls))
    check("layer: a screen read and its text come from the backend",
          orcamod._screen("h1", 12) == host.screen and ("read", "h1", 12) in host.calls
          and orcamod.screen_text("h1") == "> ")
    check("layer: focus and close are the backend's", orcamod.focus("h1")[0]
          and orcamod.close_terminal("h1", tab=False) == (True, "") and ("close", "h1", False) in host.calls)
    check("layer: repos and worktrees too", orcamod.ensure_repo("/r") == ("known", "ok")
          and ("repo", "/r", True) in host.calls and orcamod.create_worktree("/r", "n", "main") == ("/wt/new", ""))
    check("layer: runs and tasks too",
          orcamod.run_create("o") == "run-1" and orcamod.run_current() == "run-0" and orcamod.run_exists("run-1")
          and orcamod.task_create("s", "t", "d", "run-1") == "task-1" and orcamod.dispatch("task-1", "h1", "run-1") == "disp-1"
          and orcamod.has_runs())

    # Idle: the host's word, and for a vendor whose state it cannot read the screen's as well.
    host.idle = True
    check("layer: idle is the backend's wait for claude", orcamod.wait_idle("h1", 5000)
          and ("idle", "h1", 5000) in host.calls)
    host.idle = False
    check("layer: a busy host is not idle", not orcamod.wait_idle("h1", 5000))

    # What a host with no runs or labels means for the callers that gate on them.
    class Bare(FakeHost):
        supports_runs = False
        supports_labels = False
    backends.use(Bare())
    check("layer: a host without runs or labels is gated out of both",
          not orcamod.has_runs() and not orcamod.keeps_labels() and not orcamod.has_labels()
          and orcamod.host_cli(["worktree", "show"])[0] is False)
    con = db.connect()
    try:
        check("layer: so no worker is registered", spawnmod.register_worker(con, "backend", "h1") is None)
    finally:
        con.close()
    con = db.connect()
    try:
        agents.register("labelled")
        db.set_terminal(con, "labelled", None, str(home))
        check("layer: and a rename has no label to set on a host that keeps none",
              hr.label(con, "labelled") == (True, ""))
    finally:
        con.close()


def test_spawn_runs_an_agent_on_any_backend(home: Path) -> None:
    """The whole of `ocp spawn` over a backend that is not Orca: a worktree, a terminal, the first task."""
    use_home(home)
    agents.register("spawny")
    host = FakeHost()
    host.supports_runs = False
    backends.use(host)
    real = (spawnmod._run, spawnmod.prompt_landed, dmn_delivery.wait_for_session)
    try:
        spawnmod._run = lambda args, cwd=None: (0, "", "")
        spawnmod.prompt_landed = lambda agent, since, timeout_s=0: True
        dmn_delivery.wait_for_session = lambda agent, timeout_s=0: True
        ok, msg = spawnmod.spawn("spawny", ROOT, base="main", prompt="do the thing")
        kinds = [c[0] for c in host.calls]
        check("spawn: on a fake host it makes the worktree, then the terminal in it, then waits and sends",
              ok and kinds[:2] == ["worktree", "create"] and ("create", launch.command_for("spawny"), "/wt/new", "spawny") in host.calls
              and ("send", "h-new", "do the thing", True, 30) in host.calls, f"{msg} {host.calls}")
        row = db.get_agent_state(db.connect(), "spawny")
        check("spawn: and records the terminal and the worktree it got",
              row["terminal"] == "h-new" and row["worktree"] == "/wt/new", str(dict(row)))
        ok, msg = spawnmod.spawn("spawny", ROOT, base="main", dry_run=True)
        check("spawn: a dry run names the host, not Orca's commands",
              ok and "fake:" in msg and "orca terminal" not in msg and "orca worktree" not in msg, msg)
    finally:
        spawnmod._run, spawnmod.prompt_landed, dmn_delivery.wait_for_session = real


def test_no_module_outside_the_backends_starts_an_orca_process() -> None:
    """The boundary is only a boundary if nothing walks around it. Orca's CLI is spoken in ocp/session/backends
    and nowhere else; text for a person (a command to run by hand) is not a call and may name it."""
    call = re.compile(r"""\[\s*["']orca["']\s*,|which\(\s*["']orca["']|["']orca["']\s*,\s*["'](?:terminal|worktree|repo|orchestration)["']""")
    offenders = []
    for f in sorted((ROOT / "ocp").rglob("*.py")):
        if "backends" in f.parts:
            continue
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if call.search(line):
                offenders.append(f"{f.relative_to(ROOT)}:{n}")
    check("boundary: only the backends start an `orca` process", not offenders, str(offenders))
    leaks = [f"{f.relative_to(ROOT)}" for f in sorted((ROOT / "ocp").rglob("*.py"))
             if "backends" not in f.parts and re.search(r"\borca\._run\b|\borcamod\._run\b", f.read_text())]
    check("boundary: and nothing calls the Orca runner directly any more", not leaks, str(leaks))


# ---------------------------------------------------------------------------------------------------- tmux

FAKE_TMUX = r'''#!__PY__
"""A tmux with one session of panes whose screens a test writes: just the verbs the backend uses."""
import json, os, sys
state_path = os.environ["FAKE_TMUX_STATE"]
raw = sys.argv[1:]
args = raw[2:] if raw[:1] == ["-L"] else raw
try:
    st = json.load(open(state_path))
except OSError:
    st = {"up": False, "next": 0, "panes": {}, "log": [], "buffers": {}}
st["log"].append(raw)
verb, rest = args[0], args[1:]

def opt(flag, default=None):
    return rest[rest.index(flag) + 1] if flag in rest else default

def save():
    json.dump(st, open(state_path, "w"))

def fail(msg, code=1):
    save(); sys.stderr.write(msg + "\n"); sys.exit(code)

def new_pane():
    pid = "%" + str(st["next"]); st["next"] += 1
    st["panes"][pid] = {"name": opt("-n", ""), "cwd": opt("-c", ""), "screen": [], "typed": "", "sent": [],
                        "env": [rest[i + 1] for i, a in enumerate(rest) if a == "-e"]}
    st["up"] = True
    return pid

if verb == "has-session":
    if not st["up"]:
        fail("can't find session: ocp")
elif verb in ("new-session", "new-window"):
    if verb == "new-session" and st["up"]:
        fail("duplicate session: ocp")
    if verb == "new-window" and not st["up"]:
        fail("no server running on /tmp/tmux-0/ocp")
    print(new_pane())
elif verb == "list-panes":
    if not st["up"]:
        fail("no server running on /tmp/tmux-0/ocp")
    print("\n".join(st["panes"]))
elif verb == "capture-pane":
    p = st["panes"].get(opt("-t"))
    if p is None:
        fail("can't find pane: " + str(opt("-t")))
    print("\n".join(p["screen"] + [""] * max(0, 24 - len(p["screen"]))))
elif verb == "send-keys":
    p = st["panes"].get(opt("-t"))
    if p is None:
        fail("can't find pane: " + str(opt("-t")))
    if "-l" in rest:
        p["typed"] += rest[rest.index("--") + 1]
    elif rest[-1] == "Enter":
        p["sent"].append(p["typed"]); p["typed"] = ""
    else:
        p.setdefault("keys", []).append(rest[-1])
elif verb == "load-buffer":
    st["buffers"][opt("-b")] = sys.stdin.read()
elif verb == "paste-buffer":
    p = st["panes"].get(opt("-t"))
    if p is None:
        fail("can't find pane: " + str(opt("-t")))
    p["typed"] += st["buffers"].pop(opt("-b"))
    p["pasted"] = "-p" in rest
elif verb in ("kill-window", "kill-pane"):
    if opt("-t") not in st["panes"]:
        fail("can't find window: " + str(opt("-t")))
    del st["panes"][opt("-t")]
    st["up"] = bool(st["panes"])
elif verb == "select-window":
    if opt("-t") not in st["panes"]:
        fail("can't find window: " + str(opt("-t")))
else:
    fail("unknown command: " + verb, 127)
save()
'''


def _fake_tmux(td: Path) -> Path:
    d = td / "faketmux"
    d.mkdir(parents=True, exist_ok=True)
    (d / "tmux").write_text(FAKE_TMUX.replace("__PY__", sys.executable))
    (d / "tmux").chmod(0o755)
    os.environ["FAKE_TMUX_STATE"] = str(td / "tmux-state.json")
    os.environ["PATH"] = f"{d}{os.pathsep}{os.environ['PATH']}"
    return td / "tmux-state.json"


def _state(path: Path) -> dict:
    return json.loads(path.read_text())


def _screen(path: Path, pane: str, rows: list[str]) -> None:
    st = _state(path)
    st["panes"][pane]["screen"] = rows
    path.write_text(json.dumps(st))


def test_the_tmux_backend_drives_windows_over_tmux_verbs(home: Path) -> None:
    use_home(home)
    real_path = os.environ["PATH"]
    state = _fake_tmux(home)
    tm = tmuxbackend.TmuxBackend()
    tmuxbackend.ENTER_DELAY_S = 0
    try:
        check("tmux: nothing is running yet, and that is an answer: no terminals", tm.list_terminals() == set())
        os.environ["OCP_HOME"] = str(home)
        pane, why = tm.create_terminal("ocp launch backend", cwd=str(home), title="back.end:1")
        st = _state(state)
        check("tmux: the first terminal starts the session, and the command is typed into its shell, then Enter",
              pane == "%0" and why == "" and st["panes"]["%0"]["sent"] == ["ocp launch backend"]
              and st["panes"]["%0"]["cwd"] == str(home), str(st))
        made = next(a for a in st["log"] if "new-session" in a)
        check("tmux: every call is on the dedicated server socket, the session detached and of a usable size, the home passed on",
              all(a[:2] == ["-L", "ocp"] for a in st["log"]) and "-d" in made and "200" in made
              and f"OCP_HOME={home}" in st["panes"]["%0"]["env"], str(st["log"]))
        check("tmux: the window name cannot carry a `.` or `:` that tmux reads as a target",
              st["panes"]["%0"]["name"] == "back_end_1", st["panes"]["%0"]["name"])
        pane2, _ = tm.create_terminal("ocp launch reviewer")
        check("tmux: the second one is a window of the same session", pane2 == "%1"
              and any("new-window" in a for a in _state(state)["log"]))
        check("tmux: the live terminals are the pane ids", tm.list_terminals() == {"%0", "%1"})

        _screen(state, "%0", ["header", "", "last line   ", ""])
        term = tm.read_screen("%0", 3)
        check("tmux: the screen is the frame without the blank rows under it, trailing spaces cut, bottom rows kept",
              term["source"] == "screen" and term["tail"] == ["header", "", "last line"][-3:]
              and tm.read_screen("%0", 2)["tail"] == ["", "last line"], str(term))
        check("tmux: a pane that is not there reads as None, not as an empty screen",
              tm.read_screen("%99") is None and tm.read_screen("not a pane") is None)

        ok, _ = tm.send("%0", "hello there")
        ok2, _ = tm.send("%0", "")
        check("tmux: a line is typed literally and Enter is its own key; an empty text is just Enter",
              ok and ok2 and _state(state)["panes"]["%0"]["sent"][-2:] == ["hello there", ""], str(_state(state)["panes"]["%0"]))
        ok, _ = tm.send("%0", "line one\nline two", enter=False)
        p0 = _state(state)["panes"]["%0"]
        check("tmux: several lines go as one bracketed paste, and without Enter nothing is submitted",
              ok and p0["typed"] == "line one\nline two" and p0.get("pasted") is True and len(p0["sent"]) == 3, str(p0))
        tm.send("%1", "\x1b[B", enter=False)
        tm.send("%1", "\x1b", enter=False)
        check("tmux: a lone escape sequence is the key tmux names, so it arrives whole",
              _state(state)["panes"]["%1"].get("keys") == ["Down", "Escape"], str(_state(state)["panes"]["%1"]))
        check("tmux: a text starting with a dash is text, not a flag",
              tm.send("%1", "-rf --force", enter=False)[0] and _state(state)["panes"]["%1"]["typed"] == "-rf --force")
        check("tmux: a handle that is not a pane id is refused before it reaches tmux",
              tm.send("%0; kill-server", "x")[0] is False and tm.close_terminal("x y")[0] is False
              and tm.focus("../x")[0] is False)
        check("tmux: focus selects the window", tm.focus("%0")[0] is True)

        ok, _ = tm.close_terminal("%1")
        check("tmux: closing a terminal kills its window", ok and tm.list_terminals() == {"%0"})
        tm.close_terminal("%0", tab=False)
        check("tmux: and closing the last one ends the session, so the next create starts a new one",
              tm.list_terminals() == set() and tm.create_terminal("x")[0] is not None)
    finally:
        os.environ["PATH"] = real_path
        tmuxbackend.ENTER_DELAY_S = 0.4


def test_tmux_idle_is_a_screen_that_has_stopped_and_shows_no_turn_in_progress(home: Path) -> None:
    use_home(home)
    real_path = os.environ["PATH"]
    state = _fake_tmux(home)
    tm = tmuxbackend.TmuxBackend()
    saved = (tmuxbackend.IDLE_SETTLE_S, tmuxbackend.IDLE_POLL_S)
    tmuxbackend.IDLE_SETTLE_S, tmuxbackend.IDLE_POLL_S = 0.3, 0.1
    try:
        pane, _ = tm.create_terminal("x")
        _screen(state, pane, ["done.", "", "> "])
        t0 = time.monotonic()
        check("tmux idle: a screen that holds still with no turn on it is idle", tm.wait_idle(pane, 3000))
        check("tmux idle: and it takes the settle time, not the whole timeout", time.monotonic() - t0 < 2.0)
        _screen(state, pane, ["working", "* Thinking... (12s - esc to interrupt)"])
        check("tmux idle: a screen that holds still but says `esc to interrupt` is a turn in progress",
              tm.wait_idle(pane, 800) is False)
        check("tmux idle: a pane that is gone is not idle", tm.wait_idle("%99", 800) is False)
        _screen(state, pane, ["esc to interrupt"] + [f"row {i}" for i in range(20)])
        check("tmux idle: a marker scrolled far above the bottom does not count", tm.wait_idle(pane, 3000))
    finally:
        os.environ["PATH"] = real_path
        tmuxbackend.IDLE_SETTLE_S, tmuxbackend.IDLE_POLL_S = saved


def test_tmux_own_terminal_is_a_pane_on_our_server_only(home: Path) -> None:
    use_home(home)
    tm = tmuxbackend.TmuxBackend()
    env = {k: os.environ.get(k) for k in ("TMUX", "TMUX_PANE", "OCP_TMUX_SOCKET")}
    try:
        os.environ["TMUX"], os.environ["TMUX_PANE"] = "/tmp/tmux-501/ocp,123,0", "%7"
        os.environ.pop("OCP_TMUX_SOCKET", None)
        check("tmux own: a pane of our own server is the terminal, and its worktree is the directory",
              tm.own_terminal() == ("%7", os.getcwd()))
        os.environ["TMUX"] = "/tmp/tmux-501/default,123,0"
        check("tmux own: a pane of someone else's tmux means nothing on ours", tm.own_terminal() == (None, None))
        os.environ["TMUX"] = "/tmp/tmux-501/ocp,123,0"
        os.environ["TMUX_PANE"] = "%7; x"
        check("tmux own: and a value that is not a pane id is not one", tm.own_terminal() == (None, None))
        for k in ("TMUX", "TMUX_PANE"):
            os.environ.pop(k, None)
        check("tmux own: outside tmux there is none", tm.own_terminal() == (None, None))
    finally:
        for k, v in env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def test_tmux_makes_a_git_worktree_and_knows_a_repo_by_its_dot_git(home: Path) -> None:
    use_home(home)
    repo = home / "proj"
    repo.mkdir()
    genv = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
                GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")

    def git(*a):
        return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True, env=genv, timeout=30)
    git("init", "-q", "-b", "main")
    (repo / "a.txt").write_text("1\n")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    tm = tmuxbackend.TmuxBackend()
    path, why = tm.create_worktree(str(repo), "backend", "main")
    check("tmux: a worktree is a sibling directory on a new branch of its own name",
          path == str(repo.resolve().parent / "proj.worktrees" / "backend") and why == ""
          and (Path(path) / "a.txt").exists()
          and git("-C", path, "branch", "--show-current").stdout.strip() == "backend", f"{path} {why}")
    path2, _ = tm.create_worktree(str(repo), "backend", "main")
    check("tmux: a name already taken becomes name-2 and the first is left alone",
          path2 and path2.endswith("backend-2") and (Path(path) / "a.txt").exists(), str(path2))
    bad, why = tm.create_worktree(str(repo), "other", "no-such-ref")
    check("tmux: a base that is not there is refused with git's words", bad is None and "worktree create failed" in why, why)
    check("tmux: a git repository is known, anything else is not and is not made one",
          tm.ensure_repo(str(repo))[0] == "known" and tm.ensure_repo(str(home))[0] == "not_git")
    check("tmux: it keeps no runs and no labels",
          not tm.supports_runs and not tm.supports_labels and tm.run_create("o") is None
          and tm.run_current() is None and tm.dispatch("t", "%0", "r") is None
          and tm.host_cli(["x"])[0] is False)


def test_a_real_tmux_runs_the_same_calls(home: Path) -> None:
    """Against the real tmux, on a socket of its own; skipped, loudly, where tmux cannot start a server
    (a sandbox that forbids the socket, or no tmux at all)."""
    use_home(home)
    if shutil.which("tmux") is None:
        print("SKIPPED  real tmux: tmux is not installed")
        return
    sock = f"ocp-test-{os.getpid()}"
    os.environ["OCP_TMUX_SOCKET"] = sock
    probe = subprocess.run(["tmux", "-L", sock, "new-session", "-d", "-s", "probe", "sleep 30"],
                           capture_output=True, text=True, timeout=20)
    if probe.returncode != 0:
        print(f"SKIPPED  real tmux: it cannot start a server here ({probe.stderr.strip()[:100]})")
        os.environ.pop("OCP_TMUX_SOCKET", None)
        return
    tm = tmuxbackend.TmuxBackend()
    try:
        subprocess.run(["tmux", "-L", sock, "kill-server"], capture_output=True, timeout=20)
        pane, why = tm.create_terminal("echo ocp-hello-from-tmux", cwd=str(home), title="t1")
        check("real tmux: a terminal starts", pane is not None, why)
        deadline = time.time() + 10
        text = ""
        while time.time() < deadline and "ocp-hello-from-tmux" not in text.replace("echo ocp-hello-from-tmux", ""):
            time.sleep(0.3)
            text = "\n".join((tm.read_screen(pane) or {}).get("tail", []))
        check("real tmux: the command was typed into the shell and ran", "ocp-hello-from-tmux" in text, text)
        check("real tmux: it is listed and idle once the shell is waiting", pane in (tm.list_terminals() or set())
              and tm.wait_idle(pane, 8000))
        tm.send(pane, "echo second-line-ok")
        time.sleep(1)
        check("real tmux: a typed line lands", "second-line-ok" in "\n".join((tm.read_screen(pane) or {}).get("tail", [])))
        check("real tmux: closing it removes it", tm.close_terminal(pane)[0] and pane not in (tm.list_terminals() or {pane}))
    finally:
        subprocess.run(["tmux", "-L", sock, "kill-server"], capture_output=True, timeout=20)
        os.environ.pop("OCP_TMUX_SOCKET", None)


def main() -> int:
    def tests(td):
        run(lambda: test_the_config_key_picks_the_backend_and_a_typo_stops_instead_of_falling_back(Path(td) / "b1"))
        run(lambda: test_the_orca_backend_makes_the_same_cli_calls_it_always_did(Path(td) / "b2"))
        run(lambda: test_the_orca_backend_makes_worktrees_and_registers_repos_as_before(Path(td) / "b3"))
        run(lambda: test_the_terminal_layer_reaches_the_host_only_through_the_backend(Path(td) / "b4"))
        run(lambda: test_spawn_runs_an_agent_on_any_backend(Path(td) / "b5"))
        run(lambda: test_no_module_outside_the_backends_starts_an_orca_process())
        run(lambda: test_the_tmux_backend_drives_windows_over_tmux_verbs(Path(td) / "b6"))
        run(lambda: test_tmux_idle_is_a_screen_that_has_stopped_and_shows_no_turn_in_progress(Path(td) / "b7"))
        run(lambda: test_tmux_own_terminal_is_a_pane_on_our_server_only(Path(td) / "b8"))
        run(lambda: test_tmux_makes_a_git_worktree_and_knows_a_repo_by_its_dot_git(Path(td) / "b9"))
        run(lambda: test_a_real_tmux_runs_the_same_calls(Path(td) / "b10"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
