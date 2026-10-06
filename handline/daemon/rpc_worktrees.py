"""repos.list, worktrees.list and worktree.diff: where the agents work and what they have changed. No hl command serves
them (the web UI and the IDE do, which read git themselves today). The repos are the ones the agents were told to work in
(`repo` in agent.yaml); the worktrees are the ones the agents' records name and that still exist, so nothing outside them is
ever listed or read. The git runs are read-only: `--no-optional-locks`, no external diff or text conversion, output bounded."""
from __future__ import annotations

import os
from pathlib import Path

from .. import actions, db
from ..infra import gitrun, paths
from ..team import agents
from ..team import spawn as spawnmod
from . import rpc

MAX_CHANGED, MAX_DIFF_CHARS = 200, 200_000
GIT = ("--no-optional-locks",)


def _git(path: str, *args: str, timeout: int = 20) -> tuple[bool, str]:
    rc, out, _ = gitrun.run(path, *GIT, *args, timeout=timeout)
    return rc == 0, out


def _default_branch(repo: str) -> str:
    ok, out = _git(repo, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD")
    return out.strip().removeprefix("origin/") if ok and out.strip() else gitrun.INTEGRATION_BRANCH


def repos() -> list[dict]:
    seen: dict[str, dict] = {}
    for name in paths.list_agents():
        repo = agents.repo_of(name)
        if repo and repo not in seen and Path(repo).is_dir():
            seen[repo] = {"name": Path(repo).name, "path": repo, "default_branch": _default_branch(repo)}
    return sorted(seen.values(), key=lambda r: r["path"])


def known_worktrees(con) -> dict[str, str]:
    """path -> the agent that works there, for every agent whose recorded worktree exists."""
    out: dict[str, str] = {}
    for name in paths.list_agents():
        row = db.get_agent_state(con, name)
        wt = spawnmod.worktree_path(row["worktree"] if row else None)
        if wt:
            out[os.path.realpath(wt)] = name
    return out


def _changed(path: str) -> list[dict]:
    ok, status = _git(path, "status", "--porcelain=v1", "-uall")
    if not ok:
        return []
    ok, stat = _git(path, "diff", "--numstat", "--no-ext-diff", "HEAD")
    counts = {}
    for line in stat.splitlines() if ok else []:
        a, _, rest = line.partition("\t")
        d, _, name = rest.partition("\t")
        counts[name] = (int(a) if a.isdigit() else 0, int(d) if d.isdigit() else 0)
    rows = []
    for line in status.splitlines()[:MAX_CHANGED]:
        code, name = line[:2], line[3:].split(" -> ")[-1]
        mark = "?" if code == "??" else next((c for c in "RAD" if c in code), "M")
        rows.append({"path": name, "status": mark, "added": counts.get(name, (0, 0))[0], "deleted": counts.get(name, (0, 0))[1]})
    return rows


def _supervisor(agent: str) -> str | None:
    try:
        return actions.supervisor_of(actions.routing(), agent)
    except Exception:  # noqa: BLE001 - an unreadable team.yaml means no parent, not a failed read
        return None


def _worktree(path: str, owner: str, known: dict[str, str]) -> dict:
    """One worktree as the spec's `Worktree`. Its parent is the worktree of the owner's supervisor and its children those of
    the owner's reports, so the tree is the team's."""
    ok, branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
    changed = _changed(path)
    boss = _supervisor(owner)
    repo = agents.repo_of(owner)
    return {"path": path, "repo": Path(repo).name if repo else "", "branch": branch.strip() if ok else "", "owner": owner,
            "parent": next((p for p, o in known.items() if o == boss), None) if boss else None,
            "children": sorted(p for p, o in known.items() if _supervisor(o) == owner),
            "status": "dirty" if changed else "clean", "changed": changed}


def _repos_list(ctx, params) -> dict:
    return {"repos": repos()}


def _worktrees_list(ctx, params) -> dict:
    repo, owner = params.get("repo"), params.get("owner")
    for k, v in (("repo", repo), ("owner", owner)):
        if v is not None and not isinstance(v, str):
            raise rpc.bad_request(f"{k} is a string", k, rpc.INVALID_PARAMS)
    con = db.connect()
    try:
        known = known_worktrees(con)
    finally:
        con.close()
    out = []
    for path, who in sorted(known.items()):
        wt = _worktree(path, who, known)
        if (owner is None or who == owner) and (repo is None or repo in (wt["repo"], agents.repo_of(who))):
            out.append(wt)
    return {"worktrees": out}


def _diff(ctx, params) -> dict:
    path, file = params.get("path"), params.get("file")
    if not isinstance(path, str) or not path:
        raise rpc.bad_request("path is a worktree path", "path", rpc.INVALID_PARAMS)
    if file is not None and (not isinstance(file, str) or not file or file.startswith(("/", "-")) or ".." in Path(file).parts
                             or "\0" in file):
        raise rpc.bad_request("file is a path inside the worktree", "file", rpc.INVALID_PARAMS)
    con = db.connect()
    try:
        known = known_worktrees(con)
    finally:
        con.close()
    root = os.path.realpath(path)
    if root not in known:
        raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"{path} is not a worktree an agent works in", what="worktree", path=path)
    args = ["diff", "--no-ext-diff", "--no-textconv", "--no-color", "HEAD"] + (["--", file] if file else [])
    ok, out = _git(root, *args)
    if file and not out and not (Path(root) / file).is_symlink() and (Path(root) / file).is_file() and not _git(root, "ls-files", "--error-unmatch", "--", file)[0]:
        _, out = _git(root, "diff", "--no-index", "--no-ext-diff", "--no-textconv", "--no-color", "--", os.devnull, file)
    return {"path": path, "file": file, "diff": out[:MAX_DIFF_CHARS], "truncated": len(out) > MAX_DIFF_CHARS}


rpc.METHODS["repos.list"] = rpc.Method(_repos_list, "member")
rpc.METHODS["worktrees.list"] = rpc.Method(_worktrees_list, "member")
rpc.METHODS["worktree.diff"] = rpc.Method(_diff, "member")
