"""repos.list, worktrees.list and worktree.diff: where the agents work and what they have changed. No hl command serves
them (the web UI and the IDE do, which read git themselves today). The repos are the ones the agents were told to work in
(`repo` in agent.yaml); the worktrees are the ones the agents' records name and that still exist, so nothing outside them is
ever listed or read. The git runs are read-only: `--no-optional-locks`, no external diff or text conversion, output bounded.

What the spec leaves open is read as: a worktree's `parent` and `children` are the worktrees of the owner's supervisor and of
its reports (team.yaml's supervisor edges), not a host's worktree tree; `status` is `dirty` when anything is changed, else
`clean`; a changed file's counts are git's numstat against HEAD (an untracked file has 0 and 0); a `file` that is, or passes
through, a symbolic link is refused before any git runs."""
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
        for wt in spawnmod.all_paths(row):
            out[os.path.realpath(wt)] = name
    return out


def _changed(path: str) -> list[dict]:
    """The changed files with git's own status letter and line counts. Both runs are NUL-separated, so a name with a space or a
    non-ASCII character is the same string in each."""
    ok, status = _git(path, "status", "--porcelain=v1", "-z", "-uall")
    if not ok:
        return []
    ok, stat = _git(path, "diff", "--numstat", "-z", "--no-ext-diff", "HEAD")
    counts: dict[str, tuple[int, int]] = {}
    parts = stat.split("\0") if ok else []
    i = 0
    while i < len(parts):
        head = parts[i]
        a, _, rest = head.partition("\t")
        d, _, name = rest.partition("\t")
        if name == "" and i + 2 < len(parts) and head.count("\t") == 2:     # a rename: "a\td\t", then the old name, then the new
            name, i = parts[i + 2], i + 2
        if head:
            counts[name] = (int(a) if a.isdigit() else 0, int(d) if d.isdigit() else 0)
        i += 1
    rows, entries, i = [], status.split("\0"), 0
    while i < len(entries) and len(rows) < MAX_CHANGED:
        entry = entries[i]
        if len(entry) < 4:
            i += 1
            continue
        code, name = entry[:2], entry[3:]
        if "R" in code or "C" in code:      # the old name follows as its own entry
            i += 1
        mark = "?" if code == "??" else next((c for c in "RAD" if c in code), "M")
        rows.append({"path": name, "status": mark, "added": counts.get(name, (0, 0))[0], "deleted": counts.get(name, (0, 0))[1]})
        i += 1
    return rows


def _bosses() -> dict[str, str | None]:
    """Each agent's supervisor, read from team.yaml once for the call; empty when it cannot be read: no parent, not a failed read."""
    try:
        t = actions.routing()
        return {a: actions.supervisor_of(t, a) for a in paths.list_agents()}
    except Exception:  # noqa: BLE001
        return {}


def _worktree(path: str, owner: str, known: dict[str, str], bosses: dict[str, str | None]) -> dict:
    """One worktree as the spec's `Worktree`. Its parent is the worktree of the owner's supervisor and its children those of
    the owner's reports, so the tree is the team's, not the host's."""
    ok, branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
    changed = _changed(path)
    boss = bosses.get(owner)
    repo = agents.repo_of(owner)
    return {"path": path, "repo": Path(repo).name if repo else "", "branch": branch.strip() if ok else "", "owner": owner,
            "parent": next((p for p, o in known.items() if o == boss), None) if boss else None,
            "children": sorted(p for p, o in known.items() if bosses.get(o) == owner),
            "status": "dirty" if changed else "clean", "changed": changed}


def _through_a_link(root: str, file: str) -> bool:
    """Whether `file` is, or passes through, a symbolic link under `root`, or resolves outside it: such a path can name what
    lies elsewhere (a link to ~/.ssh in a worktree), and nothing is run for it."""
    cur = Path(root)
    for part in Path(file).parts:
        cur = cur / part
        if cur.is_symlink():
            return True
    real = os.path.realpath(cur)
    return os.path.commonpath([root, real]) != root


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
    bosses = _bosses()
    for path, who in sorted(known.items()):
        wt = _worktree(path, who, known, bosses)
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
    if file and _through_a_link(root, file):
        raise rpc.bad_request("file goes through a link: only what lies inside the worktree is read", "file", rpc.INVALID_PARAMS)
    args = ["diff", "--no-ext-diff", "--no-textconv", "--no-color", "HEAD"] + (["--", file] if file else [])
    ok, out = _git(root, *args)
    if file and not out and not (Path(root) / file).is_symlink() and (Path(root) / file).is_file() and not _git(root, "ls-files", "--error-unmatch", "--", file)[0]:
        _, out = _git(root, "diff", "--no-index", "--no-ext-diff", "--no-textconv", "--no-color", "--", os.devnull, file)
    return {"path": path, "file": file, "diff": out[:MAX_DIFF_CHARS], "truncated": len(out) > MAX_DIFF_CHARS}


rpc.METHODS["repos.list"] = rpc.Method(_repos_list, "member")
rpc.METHODS["worktrees.list"] = rpc.Method(_worktrees_list, "member")
rpc.METHODS["worktree.diff"] = rpc.Method(_diff, "member")
