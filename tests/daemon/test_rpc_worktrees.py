"""Tests: repos.list, worktrees.list, worktree.diff and agent.options (contract/protocol.md): what each lists from the
agents' own records, the real git a worktree is read through, a diff that cannot write and cannot leave a known worktree,
and the role gate. No hl command serves them.

Run on its own: python3 tests/daemon/test_rpc_worktrees.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.daemon import rpc_worktrees, socket_server  # noqa: E402
from handline.infra import gitrun, sessiontoken  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _hp  # noqa: E402


def _call(method: str, params: dict | None = None, as_agent: str | None = None) -> dict:
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: as_agent
    try:
        w = Wire()
        w.call("hello", _hp() if as_agent is None else {"client": "t", "protocol": 1})
        r = w.call(method, params or {})
        w.close()
        return r
    finally:
        sessiontoken.agent_of = real


def _code(r: dict):
    return r.get("error", {}).get("data", {}).get("code")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def _world(home: Path) -> tuple[Path, Path, Path]:
    """manager and worker (manager supervises worker), each in its own git worktree of one repo."""
    support._board_world(home)
    repo = home.parent / (home.name + "-repo")
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("one\ntwo\n")
    (repo / "b.txt").write_text("bee\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "first")
    trees = {}
    for who in ("manager", "worker"):
        wt = home.parent / f"{home.name}-wt-{who}"
        _git(repo, "worktree", "add", "-q", "-b", f"{who}-branch", str(wt))
        trees[who] = wt
        agents.register(who, repo=str(repo))
        con = db.connect()
        db.set_terminal(con, who, f"t-{who}", str(wt))
        con.close()
    return repo, trees["manager"], trees["worker"]


def test_repos_and_worktrees_list_what_the_agents_record_with_the_real_git_state(home: Path) -> None:
    repo, mgr, wkr = _world(home)
    r = _call("repos.list")["result"]["repos"]
    check("repos.list: the repo the agents work in, its name, path and default branch", r == [
        {"name": repo.name, "path": str(repo), "default_branch": "main"}], str(r))
    ws = _call("worktrees.list")["result"]["worktrees"]
    by = {w["owner"]: w for w in ws}
    check("worktrees.list: one per agent worktree, with its branch and repo", set(by) == {"manager", "worker"}
          and by["worker"]["branch"] == "worker-branch" and by["worker"]["repo"] == repo.name
          and by["worker"]["path"] == str(wkr.resolve()), str(ws))
    check("worktrees.list: a clean one says so and changed nothing", by["worker"]["status"] == "clean" and by["worker"]["changed"] == [])
    check("worktrees.list: the tree is the team's, the manager's worktree is the worker's parent",
          by["worker"]["parent"] == by["manager"]["path"] and by["manager"]["children"] == [by["worker"]["path"]]
          and by["manager"]["parent"] is None and by["worker"]["children"] == [], str(ws))
    (wkr / "a.txt").write_text("one\nTWO\nthree\n")
    (wkr / "new.txt").write_text("fresh\n")
    (wkr / "b.txt").unlink()
    w = {w["owner"]: w for w in _call("worktrees.list")["result"]["worktrees"]}["worker"]
    got = {c["path"]: c for c in w["changed"]}
    check("worktrees.list: a dirty one lists its changed files with status and line counts", w["status"] == "dirty"
          and got["a.txt"]["status"] == "M" and got["a.txt"]["added"] == 2 and got["a.txt"]["deleted"] == 1
          and got["b.txt"]["status"] == "D" and got["b.txt"]["deleted"] == 1 and got["new.txt"]["status"] == "?", str(w["changed"]))
    check("worktrees.list: owner narrows", [x["owner"] for x in _call("worktrees.list", {"owner": "manager"})["result"]["worktrees"]] == ["manager"])
    check("worktrees.list: and so does repo", len(_call("worktrees.list", {"repo": repo.name})["result"]["worktrees"]) == 2
          and _call("worktrees.list", {"repo": "other"})["result"]["worktrees"] == [])
    con = db.connect()
    db.set_terminal(con, "worker", "t-worker", str(wkr) + "-gone")
    con.close()
    check("worktrees.list: a worktree that no longer exists is not listed", [x["owner"] for x in _call("worktrees.list")["result"]["worktrees"]] == ["manager"])
    for bad in ({"repo": 3}, {"owner": ["x"]}):
        check(f"worktrees.list: {bad} is bad_request", _code(_call("worktrees.list", bad)) == "bad_request")


def test_the_diff_is_read_only_bounded_and_never_leaves_a_known_worktree(home: Path) -> None:
    repo, mgr, wkr = _world(home)
    (wkr / "a.txt").write_text("one\nTWO\nthree\n")
    (wkr / "new.txt").write_text("fresh\n")
    path = str(wkr.resolve())
    r = _call("worktree.diff", {"path": path})["result"]
    check("worktree.diff: the whole worktree's tracked changes as a unified diff", r["path"] == path and r["file"] is None
          and "-two" in r["diff"] and "+TWO" in r["diff"] and "diff --git" in r["diff"] and r["truncated"] is False, str(r))
    r = _call("worktree.diff", {"path": path, "file": "a.txt"})["result"]
    check("worktree.diff: one file narrows it", r["file"] == "a.txt" and "+TWO" in r["diff"] and "new.txt" not in r["diff"], str(r))
    r = _call("worktree.diff", {"path": path, "file": "new.txt"})["result"]
    check("worktree.diff: an untracked file is shown as all added", "+fresh" in r["diff"], str(r))
    r = _call("worktree.diff", {"path": path, "file": "b.txt"})["result"]
    check("worktree.diff: an unchanged file is an empty diff, not an error", r["diff"] == "" and r["truncated"] is False, str(r))
    real = rpc_worktrees.MAX_DIFF_CHARS
    rpc_worktrees.MAX_DIFF_CHARS = 20
    try:
        r = _call("worktree.diff", {"path": path})["result"]
        check("worktree.diff: output past the bound is cut and says so", len(r["diff"]) == 20 and r["truncated"] is True, str(r))
    finally:
        rpc_worktrees.MAX_DIFF_CHARS = real
    # refusals
    r = _call("worktree.diff", {"path": str(repo)})
    check("worktree.diff: the repo itself is not a worktree an agent works in: not_found", _code(r) == "not_found", str(r))
    r = _call("worktree.diff", {"path": "/etc"})
    check("worktree.diff: a path outside every known worktree is not_found", _code(r) == "not_found", str(r))
    r = _call("worktree.diff", {"path": path + "/.."})
    check("worktree.diff: a path that walks out of one is not_found", _code(r) == "not_found", str(r))
    for bad in ("../a.txt", "/etc/passwd", "sub/../../x", "-p", "a\0b", ""):
        r = _call("worktree.diff", {"path": path, "file": bad})
        check(f"worktree.diff: file {bad!r} is bad_request naming file", _code(r) == "bad_request" and r["error"]["data"]["field"] == "file", str(r))
    for bad in ({}, {"path": ""}, {"path": 3}):
        check(f"worktree.diff: {bad} is bad_request naming path", _code(_call("worktree.diff", bad)) == "bad_request")
    outside = home.parent / (home.name + "-outside")
    outside.mkdir()
    (outside / "key").write_text("SECRET\n")
    (wkr / "linkdir").symlink_to(outside)
    (wkr / "linkfile").symlink_to(outside / "key")
    ran_before = []
    real_run = gitrun.run
    gitrun.run = lambda cwd, *a, **k: (ran_before.append(a), real_run(cwd, *a, **k))[1]
    try:
        for bad in ("linkdir/key", "linkdir", "linkfile", "linkdir/../key"):
            r = _call("worktree.diff", {"path": path, "file": bad})
            check(f"worktree.diff: {bad!r} goes through a link and is bad_request naming file, with no git run for it",
                  _code(r) == "bad_request" and r["error"]["data"]["field"] == "file" and "SECRET" not in json.dumps(r), str(r))
        check("(and not a single git was run for any of them, the known-worktree lookup apart)", not [a for a in ran_before if "diff" in a], str(ran_before))
    finally:
        gitrun.run = real_run
    # it cannot write: every git it runs is read-only
    ran: list = []
    real_run = gitrun.run
    gitrun.run = lambda cwd, *a, **k: (ran.append(a), real_run(cwd, *a, **k))[1]
    try:
        index_before = (wkr / ".git").read_text() if (wkr / ".git").is_file() else ""
        _call("worktree.diff", {"path": path})
        _call("worktrees.list")
    finally:
        gitrun.run = real_run
    verbs = {a[1] for a in ran if len(a) > 1}
    check("read-only: every git run takes no optional locks", ran and all(a[0] == "--no-optional-locks" for a in ran), str(ran[:3]))
    check("read-only: and only reads", verbs <= {"diff", "status", "rev-parse", "ls-files", "symbolic-ref"}, str(verbs))
    check("read-only: diff never asks for an external diff or a text conversion", all("--no-ext-diff" in a and "--no-textconv" in a for a in ran if len(a) > 1 and a[1] == "diff" and "--numstat" not in a), str([a for a in ran if len(a) > 1 and a[1] == "diff"]))


def test_names_with_spaces_and_non_ascii_carry_their_line_counts_and_the_team_is_read_once(home: Path) -> None:
    from handline import actions as act
    repo, mgr, wkr = _world(home)
    for name in ("a b.txt", "caf\u00e9.txt"):
        (wkr / name).write_text("one\n")
        _git(wkr, "add", name)
    _git(wkr, "commit", "-q", "-m", "names")
    (wkr / "a b.txt").write_text("one\ntwo\nthree\n")
    (wkr / "caf\u00e9.txt").write_text("")
    calls = {"n": 0}
    real = act.routing
    act.routing = lambda: (calls.__setitem__("n", calls["n"] + 1), real())[1]
    try:
        w = {x["owner"]: x for x in _call("worktrees.list")["result"]["worktrees"]}["worker"]
    finally:
        act.routing = real
    got = {c["path"]: c for c in w["changed"]}
    check("worktrees.list: a name with a space keeps its status and line counts", got["a b.txt"]["status"] == "M" and got["a b.txt"]["added"] == 2, str(got))
    check("worktrees.list: and so does a non-ASCII one", got["caf\u00e9.txt"]["status"] == "M" and got["caf\u00e9.txt"]["deleted"] == 1, str(got))
    check("worktrees.list: team.yaml is read once for the call, not once per pair of worktrees", calls["n"] == 1, str(calls))


def test_agent_options_offers_what_exists(home: Path) -> None:
    repo, mgr, wkr = _world(home)
    (paths.agent_dir("worker") / "ROLE.md").write_text("# worker\n\nBuilds what the manager asks for and shows the sha.\n")
    r = _call("agent.options")["result"]
    check("agent.options: the repos agents work in", r["repos"] == [{"name": repo.name, "path": str(repo), "default_branch": "main"}], str(r["repos"]))
    check("agent.options: a role per agent, with its ROLE.md's first line of substance", {"name": "worker", "description": "Builds what the manager asks for and shows the sha."} in r["roles"]
          and {x["name"] for x in r["roles"]} == {"manager", "worker"}, str(r["roles"]))
    check("agent.options: the models and efforts agent settings accept", "opus" in r["models"] and r["efforts"] == list(agents.VALID_EFFORT), str((r["models"], r["efforts"])))
    check("agent.options: every account with its login state", all(a["login_state"] in ("ok", "needs_login", "unknown") for a in r["accounts"]), str(r["accounts"]))
    check("agent.options: no declared name left unregistered, none offered", r["free_names"] == [], str(r["free_names"]))
    hand_edit_team('''
agents:
  manager: {}
  worker:  {}
  newcomer: {}

edges:
  - { from: "manager", to: "worker", relation: supervisor, response: fire_and_forget }

limits:
  max_chain_depth: 4
''')
    check("agent.options: a name team.yaml declares that no agent has is free", _call("agent.options")["result"]["free_names"] == ["newcomer"])


def test_these_methods_are_a_persons_and_an_agent_session_is_refused(home: Path) -> None:
    _world(home)
    for method, params in (("repos.list", {}), ("worktrees.list", {}), ("worktree.diff", {"path": "/x"}), ("agent.options", {})):
        r = _call(method, params, as_agent="manager")
        check(f"{method}: an agent session is refused by role", r.get("error", {}).get("data", {}).get("gate") == "role", str(r))
    check("no hl command serves them: the spec table x-agent-commands names none of the four",
          not {"repos.list", "worktrees.list", "worktree.diff", "agent.options"} & set(json.loads(Path("contract/protocol.json").read_text())["x-agent-commands"].values()))


def test_the_worktree_fixtures_pass_against_this_daemon(home: Path) -> None:
    import socket
    import threading
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "contract"))
    import conformance

    def connect():
        a, b = socket.socketpair()
        threading.Thread(target=socket_server._serve_one, args=(b, lambda m: None), daemon=True).start()
        return a
    _world(home)
    real = sessiontoken.agent_of
    results = []
    try:
        for role in ("founder", "agent"):
            sessiontoken.agent_of = (lambda pid: "manager") if role == "agent" else (lambda pid: None)
            results += [(*r, role) for r in conformance.run_all([Path("contract/fixtures/worktrees.json")], {role: connect},
                                                                 {"founder_key": _hp()["key"]}) if r[0] != "skip"]
    finally:
        sessiontoken.agent_of = real
    bad = [(n, why) for st, why, n, _ in results if st != "pass"]
    check("fixtures: every worktree fixture passes against this daemon, each as its own role", not bad and results, str(bad))


def main() -> int:
    def tests(td):
        run(lambda: test_repos_and_worktrees_list_what_the_agents_record_with_the_real_git_state(Path(td) / "w0"))
        run(lambda: test_the_diff_is_read_only_bounded_and_never_leaves_a_known_worktree(Path(td) / "w1"))
        run(lambda: test_names_with_spaces_and_non_ascii_carry_their_line_counts_and_the_team_is_read_once(Path(td) / "w5"))
        run(lambda: test_agent_options_offers_what_exists(Path(td) / "w2"))
        run(lambda: test_these_methods_are_a_persons_and_an_agent_session_is_refused(Path(td) / "w3"))
        run(lambda: test_the_worktree_fixtures_pass_against_this_daemon(Path(td) / "w4"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
