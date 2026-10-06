"""repos.list, worktrees.list, worktree.diff, agent.options."""
from . import err, fake


@fake("repos.list", "worktrees.list", "worktree.diff", "agent.options")
def worktrees(role, method, params):
    if method == "worktrees.list":
        for k in ("repo", "owner"):
            if k in params and not isinstance(params[k], str):
                return "fail", err(-32602, "bad_request", field=k)
    if method == "worktree.diff":
        f = params.get("file")
        if f is not None and (not isinstance(f, str) or not f or f.startswith(("/", "-")) or ".." in f.split("/")):
            return "fail", err(-32602, "bad_request", field="file")
        if not isinstance(params.get("path"), str) or not params["path"]:
            return "fail", err(-32602, "bad_request", field="path")
        return "fail", {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": "worktree"}}
    return "ok", {"repos": [], "worktrees": [], "roles": [], "models": [], "efforts": [], "accounts": [], "free_names": []}
