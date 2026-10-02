"""The Orca backend: every call is one `orca` CLI invocation."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess

from ...infra import textsafe
from .base import Backend

# `--tab` waits for Orca to persist the layout; a wedged Orca must not hold the tick.
CLOSE_TIMEOUT_S = 30


def _exec(argv: list[str], timeout: int = 120) -> tuple[int, str, str]:
    """One command, as (exit code, stdout, stderr), each clean at the source, so a caller that
    embeds either in a message need not sanitise it. The one place an `orca` process starts.
    It does not cover a caller that `json.loads` the text: a control byte sent escaped comes
    back real from the parse, so such a caller reads the value through `_parsed`."""
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return (r.returncode, textsafe.oneline(r.stdout.strip()), textsafe.oneline(r.stderr.strip()))


def _run(args: list[str], timeout: int = 180) -> tuple[bool, str]:
    """`orca <args>` as (ok, its text, or the reason it could not run). `--json` output survives the
    cleaning: JSON is whitespace-insensitive outside its strings, and a conformant writer already
    escapes a control byte inside one rather than emitting it raw."""
    try:
        code, out, err = _exec(["orca", *args], timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return False, textsafe.oneline(str(e))
    return code == 0, out or err


def _result(out: str, *path: str):
    """Dig one value out of an `--json` reply, or None."""
    try:
        node = json.loads(out).get("result", {})
    except (json.JSONDecodeError, TypeError):
        return None
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _parsed(out: str, *keys: str) -> str | None:
    """One string out of Orca's `--json`, or None if it is not usable.

    A worktree path and a terminal handle are identifiers: stored in the db,
    handed back to Orca, and named in messages written much later by callers
    that no longer see where the value came from. Sanitising one here would
    keep a name for something that does not exist, and leave every sink
    downstream to remember. So one carrying a control byte is refused instead,
    which is what a value that could not be read at all already does.
    """
    try:
        value = json.loads(out)
        for k in keys:
            value = value[k]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    if not isinstance(value, str) or textsafe.has_unsafe(value):
        return None
    return value


def _repo_paths(out: str) -> set[str] | None:
    """Every real path `orca repo list --json` names, or None if it is not the
    JSON of a successful reply. Read for any `path` string wherever it sits, so
    a change in how Orca nests the list does not lose a repo."""
    try:
        reply = json.loads(out)
    except json.JSONDecodeError:
        return None
    if not isinstance(reply, dict) or not reply.get("ok"):
        return None
    found: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "path" and isinstance(v, str):
                    found.add(os.path.realpath(v))
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(reply.get("result"))
    return found


class OrcaBackend(Backend):
    name = "orca"
    supports_runs = True
    supports_labels = True

    def available(self) -> bool:
        return shutil.which("orca") is not None

    def own_terminal(self) -> tuple[str | None, str | None]:
        return os.environ.get("ORCA_TERMINAL_HANDLE"), os.environ.get("ORCA_WORKTREE_ID")

    def create_terminal(self, command: str, cwd: str | None = None,
                        title: str | None = None) -> tuple[str | None, str]:
        """`cwd` None is Orca's "current" selector: whatever worktree this process is already in,
        which is what a throwaway probe wants."""
        selector = "current" if cwd is None else f"path:{cwd}"
        argv = ["orca", "terminal", "create", "--worktree", selector]
        if title:
            argv += ["--title", title]
        argv += ["--command", command]
        if cwd is not None:
            argv.append("--json")
        try:
            code, out, err = _exec(argv, timeout=180)
        except (OSError, subprocess.SubprocessError) as e:
            return None, textsafe.oneline(str(e))
        if code != 0:
            return None, err or out or f"orca exited {code}"
        return _parsed(out, "result", "terminal", "handle"), ""

    def close_terminal(self, handle: str, tab: bool = True) -> tuple[bool, str]:
        args = ["terminal", "close", "--terminal", handle]
        if tab:
            args.append("--tab")
        return _run(args, timeout=CLOSE_TIMEOUT_S)

    def list_terminals(self) -> set[str] | None:
        ok, out = _run(["terminal", "list", "--json"])
        if not ok:
            return None
        try:
            terms = json.loads(out)["result"]["terminals"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return None
        return {t["handle"] for t in terms if isinstance(t, dict) and t.get("handle")}

    def read_screen(self, handle: str, limit: int = 40) -> dict | None:
        ok, out = _run(["terminal", "read", "--terminal", handle, "--screen",
                        "--limit", str(limit), "--json"], timeout=30)
        term = _result(out, "terminal") if ok else None
        return term if isinstance(term, dict) else None

    def send(self, handle: str, text: str, enter: bool = True,
             wait_submit_s: int | None = None) -> tuple[bool, str]:
        args = ["terminal", "send", "--terminal", handle, "--text", text]
        if enter:
            args.append("--enter")
        if wait_submit_s is not None:
            args += ["--wait-submit", str(wait_submit_s)]
        return _run(args)

    def wait_idle(self, handle: str, timeout_ms: int) -> bool:
        """`ok` only says the wait command ran; `result.wait.satisfied` is the answer."""
        ok, out = _run(
            ["terminal", "wait", "--terminal", handle, "--for", "tui-idle",
             "--timeout-ms", str(timeout_ms), "--json"],
            timeout=timeout_ms // 1000 + 30,
        )
        if not ok:
            return False
        try:
            return bool(json.loads(out)["result"]["wait"]["satisfied"])
        except (json.JSONDecodeError, KeyError, TypeError):
            return False

    def host_cli(self, args: list[str], timeout: int = 180) -> tuple[bool, str]:
        return _run(args, timeout=timeout)

    def focus(self, handle: str) -> tuple[bool, str]:
        return _run(["terminal", "switch", "--terminal", handle], timeout=20)

    def ensure_repo(self, path: str, present: bool = True) -> tuple[str, str]:
        """Make sure Orca has `path` as a repo: (state, what to tell the founder).

        state is 'known' (already there, left alone), 'added', 'not_git' (Orca is
        not asked: a folder that is not a git repository needs the founder's
        `git init` first), 'no_orca' or 'failed'. The list is read first, so a
        repo Orca already has is never added twice."""
        path = os.path.realpath(os.path.expanduser(path))
        add = "orca repo add --path " + shlex.quote(path)
        if not present:
            return "no_orca", f"Orca is not on PATH, so {path} is not registered there: `{add}`"
        ok, out = _run(["repo", "list", "--json"])
        known = _repo_paths(out) if ok else None
        if known is None:
            return "failed", f"Orca would not list its repos ({out.strip()[:160]}); `{add}` once it does"
        if path in known:
            return "known", f"Orca already has {path}"
        if not os.path.exists(os.path.join(path, ".git")):
            return "not_git", (f"{path} is not a git repository, so Orca cannot have it: "
                               f"run `git init` there, then `{add}`")
        ok, out = _run(["repo", "add", "--path", path, "--json"])
        try:
            ok = ok and bool(json.loads(out).get("ok"))
        except (json.JSONDecodeError, AttributeError):
            ok = False
        if not ok:
            return "failed", f"Orca would not add {path} ({out.strip()[:160]}): `{add}`"
        return "added", f"Orca now has {path}"

    def create_worktree(self, repo: str, name: str, base_ref: str) -> tuple[str | None, str]:
        create = ["orca", "worktree", "create", "--repo", f"path:{repo}",
                  "--name", name, "--base-branch", base_ref, "--json"]
        code, out, err = _exec(create)
        if code != 0 and "repo_not_found" in f"{out} {err}":
            # Orca does not have this folder as a repo yet: register it (never
            # one it already has, nor a folder without `git init`) and try once more.
            state, said = self.ensure_repo(repo)
            if state != "added":
                return None, f"worktree create failed: {err or out} - {said}"
            code, out, err = _exec(create)
        if code != 0:
            return None, f"worktree create failed: {err or out}"
        wt = _parsed(out, "result", "worktree", "path")
        if wt is None:
            return None, f"could not read the new worktree path from: {out[:200]}"
        return wt, ""

    # -- orchestration: the Run is the card a team's work hangs under
    def run_create(self, objective: str) -> str | None:
        """It also *binds* the calling terminal to that Run, which is why this is
        called once and remembered: calling it again makes a second card with the
        same name and moves the binding to it, and a team split across two cards
        is a tree that draws half of itself."""
        ok, out = _run(["orchestration", "run-create", "--objective", objective, "--json"])
        if not ok:
            return None
        return _result(out, "run", "id") or _result(out, "id")

    def run_current(self) -> str | None:
        ok, out = _run(["orchestration", "run-current", "--json"])
        return _result(out, "run", "id") if ok else None

    def run_exists(self, run: str) -> bool:
        """`--id`, not `--run`: the flag differs per verb, and reading the wrong
        one as "gone" made a new Run for every agent."""
        ok, out = _run(["orchestration", "run-show", "--id", run, "--json"])
        return ok and bool(_result(out, "run", "id"))

    def run_use(self, run: str) -> bool:
        ok, _out = _run(["orchestration", "run-use", "--id", run, "--json"])
        return ok

    def task_create(self, spec: str, title: str, display_name: str, run: str,
                    parent: str | None = None) -> str | None:
        args = ["orchestration", "task-create", "--spec", spec, "--task-title", title,
                "--display-name", display_name, "--run", run, "--json"]
        if parent:
            args += ["--parent", parent]
        ok, out = _run(args)
        if not ok:
            return None
        return _result(out, "task", "id") or _result(out, "id")

    def dispatch(self, task: str, handle: str, run: str) -> str | None:
        """No `--inject`: the terminal is already running `ocp launch`, and the spec
        is metadata for the card rather than an instruction for the agent. Orca
        injecting it too would deliver the same thing twice."""
        ok, out = _run(["orchestration", "dispatch", "--task", task, "--to", handle,
                        "--run", run, "--json"])
        if not ok:
            return None
        return _result(out, "dispatch", "id") or _result(out, "dispatchId") or task
