"""Landing a branch on main, done by the daemon - which runs none of the branch's code.

The daemon is outside every agent's sandbox, so it alone can write main's checkout; whatever it runs
runs unsandboxed. It therefore runs only git. The test is the merger's, in the merger's sandbox
(`tools/land.sh --check`), and it names the tree it tested. A sha lands when
  - the auditor recorded a PASS for that exact sha (`ocp land --pass`), and
  - the merger recorded it with the tree its test saw (`ocp land <sha> <message>`).
Both are accepted only from the process of that agent's session (peer.py), never from the name in the
request. The daemon merges the sha into main itself, and fast-forwards main only if the result is the
tree that was tested; if main moved meanwhile the trees differ and the merger tests again. A sha lands
at most once.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
from pathlib import Path

from .. import db
from ..infra import gitrun, paths, textsafe
from ..team import ask as askmod, team

REQUESTER = "land"
MERGER = "orcapipe.merger"
AUDITOR = "orcapipe.auditor"
OUTPUT_KEPT = 1500

_worker: dict = {"thread": None, "swept": False}


def repo() -> Path:
    return Path(os.environ.get("OCP_LAND_REPO") or paths.repo_root())


def _git(*args: str, cwd=None, timeout: int = 120) -> tuple[int | None, str, str]:
    return gitrun.run(cwd or repo(), *args, timeout=timeout)


def _commit(sha: str) -> tuple[str | None, str]:
    rc, out, err = _git("rev-parse", "--verify", f"{sha}^{{commit}}")
    return (out.strip(), "") if rc == 0 else (None, f"{sha} is not a commit here: {textsafe.oneline(err or out)}")


def record_pass(con, sha: str, caller: str | None) -> tuple[bool, str]:
    """The auditor's PASS for `sha`."""
    if caller != AUDITOR:
        return False, f"{caller or 'a process that is no agent session'} may not record a PASS; the auditor does"
    full, why = _commit(sha)
    if full is None:
        return False, why
    con.execute("INSERT OR REPLACE INTO land_passes(sha, by, ts) VALUES(?, ?, ?)", (full, caller, int(time.time())))
    return True, f"PASS recorded for {full[:8]}"


def request(con, sha: str, message: str, tree: str | None, caller: str | None) -> tuple[bool, str]:
    """Queue `sha` to be landed with `message`, the merger having tested a merge whose tree is `tree`."""
    if caller != MERGER:
        return False, f"{caller or 'a process that is no agent session'} may not land a branch; the merger does"
    message = (message or "").strip()
    if not message or not (tree or "").strip():
        return False, "a land needs the merge message and the tree the test saw: use `ocp land <sha> <message>`"
    full, why = _commit(sha)
    if full is None:
        return False, why
    if con.execute("SELECT 1 FROM land_passes WHERE sha = ?", (full,)).fetchone() is None:
        return False, f"no auditor PASS is recorded for {full[:8]}; a PASS for another sha does not count"
    seen = con.execute("SELECT id, state, merged FROM lands WHERE sha = ? AND state != 'failed' "
                       "ORDER BY id DESC LIMIT 1", (full,)).fetchone()
    if seen is not None:
        where = f"landed as {seen['merged'][:8]}" if seen["state"] == "landed" else seen["state"]
        return False, f"{full[:8]} is already {where} (land #{seen['id']}); a sha lands once"
    rc, _, _ = _git("merge-base", "--is-ancestor", full, "main")
    if rc == 0:
        return False, f"{full[:8]} is already on main"
    cur = con.execute("INSERT INTO lands(sha, message, tree, requester, state, ts) VALUES(?, ?, ?, ?, 'queued', ?)",
                      (full, message, tree.strip(), caller, int(time.time())))
    ahead = con.execute("SELECT COUNT(*) FROM lands WHERE state IN ('queued', 'running') AND id < ?",
                        (cur.lastrowid,)).fetchone()[0]
    return True, f"land #{cur.lastrowid}: {full[:8]} queued" + (f", {ahead} ahead of it" if ahead else "")


def _tell(con, to: str, body: str, log) -> None:
    rid = db.add_request(con, REQUESTER, to, body, None, None, "fire_and_forget", "say", 0, None)
    db.mark_send_now(con, rid)
    askmod.deliver(rid, REQUESTER, to, body, None,
                   team.Decision(True, "land", kind="say", response="fire_and_forget", chain_depth=0))
    askmod.push(con, to, rid)


def _land(row) -> tuple[bool, str]:
    """Merge `row.sha` into main in a scratch worktree, git only, and fast-forward main when the merge
    is the tree that was tested. (landed, merge sha or why not)."""
    rc, out, _ = _git("worktree", "list", "--porcelain")
    main_dir = None
    cur = None
    for line in (out or "").splitlines():
        if line.startswith("worktree "):
            cur = line[9:]
        elif line == "branch refs/heads/main":
            main_dir = cur
    if rc != 0 or not main_dir:
        return False, "no worktree has main checked out"
    work = tempfile.mkdtemp(prefix="land.")
    try:
        if _git("worktree", "add", "--detach", work, "main", cwd=main_dir)[0] != 0:
            return False, "could not make a scratch worktree"
        if _git("merge", "--no-ff", "-q", "-m", row["message"], row["sha"], cwd=work)[0] != 0:
            rc, files, _ = _git("diff", "--name-only", "--diff-filter=U", cwd=work)
            return False, f"conflicts with main in: {' '.join(files.split())}"
        _, tree, _ = _git("rev-parse", "HEAD^{tree}", cwd=work)
        if tree.strip() != row["tree"]:
            return False, "main moved since the merger's test: the merge is not the tree it tested; test again"
        _, merged, _ = _git("rev-parse", "HEAD", cwd=work)
        if _git("merge", "--ff-only", "-q", merged.strip(), cwd=main_dir)[0] != 0:
            return False, "main moved while this ran (or its checkout is dirty); nothing landed"
        return True, merged.strip()
    finally:
        _git("worktree", "remove", "--force", work, cwd=main_dir)
        if Path(work).exists():
            import shutil
            shutil.rmtree(work, ignore_errors=True)


def _run_one(row, log) -> None:
    con = db.connect()
    try:
        landed, out = _land(row)
        con.execute("UPDATE lands SET state = ?, merged = ?, output = ?, done_at = ? WHERE id = ?",
                    ("landed" if landed else "failed", out if landed else None, out[-OUTPUT_KEPT:],
                     int(time.time()), row["id"]))
        short = row["sha"][:8]
        body = (f"land #{row['id']}: {short} is on main as {out[:8]}." if landed else
                f"land #{row['id']}: {short} did not land. {textsafe.oneline(out)} "
                f"Test again and send it with `ocp land`.")
        log(f"  land #{row['id']}: {'landed' if landed else 'failed'}")
        _tell(con, row["requester"], body, log)
    except Exception as e:  # noqa: BLE001 - the row must not stay 'running' for a message that failed to send
        log(f"  land #{row['id']}: {type(e).__name__}: {e}")
    finally:
        con.close()


def sweep(con, log) -> None:
    """Start the oldest queued land if none is running. A row left 'running' by a daemon that
    stopped mid-land goes back in the queue: main changes only by the fast-forward at the end."""
    t = _worker["thread"]
    if t is not None and t.is_alive():
        return
    if not _worker["swept"]:
        con.execute("UPDATE lands SET state = 'queued' WHERE state = 'running'")
        _worker["swept"] = True
    row = con.execute("SELECT * FROM lands WHERE state = 'queued' ORDER BY id LIMIT 1").fetchone()
    if row is None:
        return
    con.execute("UPDATE lands SET state = 'running' WHERE id = ?", (row["id"],))
    _worker["thread"] = threading.Thread(target=_run_one, args=(row, log), daemon=True)
    _worker["thread"].start()
