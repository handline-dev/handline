"""Landing a branch on main, done by the daemon - which runs none of the branch's code.

The daemon is outside every agent's sandbox, so it alone can write main's checkout; whatever it runs
runs unsandboxed. It therefore runs only git, and main's own tools/localrefs.py on the message it is
given. The test is the merger's, in the merger's sandbox (`tools/land.sh --check`), and it names the
tree it tested. A sha lands when
  - an auditor (any agent named `<team>.auditor`) recorded a PASS for that exact sha (`hl land --pass`), and
  - a merger (any agent named `<team>.merger`) recorded it with the tree its test saw (`hl land <sha> <message> --tree <tree>`).
The merger may also fix what is not the auditor's to judge - a list-file or README conflict, a missing doc line -
with `--fix <dir>`: whole files on main's tools/housekeeping.paths, written over the merge; anything else needs a
PASS. With `--squash` main gets one commit carrying the merger's message, so a WIP branch message never lands.
Both are accepted only from a process of the session `hl launch` recorded for that agent (found by
the socket's peer pid, infra/sessiontoken.py), never on the name in the request. The daemon merges the sha into main itself, and fast-forwards main only if the result is the
tree that was tested; if main moved meanwhile the trees differ and the merger tests again. A sha lands
at most once.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from .. import db
from ..infra import gitrun, housekeeping, names, paths, textsafe
from ..team import ask as askmod, team

REQUESTER = "land"
MERGER_ROLE = "merger"
AUDITOR_ROLE = "auditor"
OUTPUT_KEPT = 1500

_worker: dict = {"thread": None, "swept": False}


def repo() -> Path:
    return Path(names.getenv("OCP_LAND_REPO") or paths.repo_root())


def _git(*args: str, cwd=None, timeout: int = 120) -> tuple[int | None, str, str]:
    return gitrun.run(cwd or repo(), *args, timeout=timeout)


def _commit(sha: str) -> tuple[str | None, str]:
    rc, out, err = _git("rev-parse", "--verify", f"{sha}^{{commit}}")
    return (out.strip(), "") if rc == 0 else (None, f"{sha} is not a commit here: {textsafe.oneline(err or out)}")


def _message_refusal(message: str) -> str | None:
    """Why `message` may not be a merge message (personal data or a machine-local id, tools/localrefs.py),
    None when it may. Run from main's checkout, never from the branch being landed."""
    tool = repo() / "tools" / "localrefs.py"
    if not tool.is_file():
        return None
    with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8") as f:
        f.write(message)
        f.flush()
        try:
            r = subprocess.run([sys.executable, str(tool), "message", "--verbatim", f.name],
                               capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as e:
            return f"the message could not be checked ({type(e).__name__}); try again"
    return None if r.returncode == 0 else textsafe.oneline(r.stderr)[:400]


def is_auditor(agent: str | None) -> bool:
    """Whether `agent` holds the auditor role: a name's last part is its role, numbered when a team has more than
    one (`<team>.auditor`, `<team>.auditor2`), and the roster the founder approves is the only place such a name
    comes from. Who is calling is not this name's claim: the daemon has already matched it to the session `hl launch`
    recorded. Any team's auditor may pass any team's sha, so registering a `<team>.auditor` grants PASS for every
    branch."""
    return (bool(agent) and team.parent_name(agent) is not None
            and re.fullmatch(rf"{AUDITOR_ROLE}\d*", agent.rpartition(".")[2]) is not None)


def is_merger(agent: str | None) -> bool:
    """Whether `agent` holds the merger role (`<team>.merger`), named and vouched for as `is_auditor` is. Any team's
    merger may land."""
    return bool(agent) and team.parent_name(agent) is not None and agent.rpartition(".")[2] == MERGER_ROLE


def record_pass(con, sha: str, caller: str | None) -> tuple[bool, str]:
    """The auditor's PASS for `sha`, from the auditor of any team."""
    if not is_auditor(caller):
        return False, f"{caller or 'someone else'} may not record a PASS; the auditor does"
    full, why = _commit(sha)
    if full is None:
        return False, why
    con.execute("INSERT OR REPLACE INTO land_passes(sha, by, ts) VALUES(?, ?, ?)", (full, caller, int(time.time())))
    return True, f"PASS recorded for {full[:8]}"


FIX_MAX_BYTES = 1 << 20


def housekeeping_paths() -> list[str]:
    """main's tools/housekeeping.paths: the globs a merger may fix at land time. Read from main's checkout, which
    the daemon alone writes, never from a branch."""
    try:
        return housekeeping.read_globs((repo() / "tools" / "housekeeping.paths").read_text(encoding="utf-8"))
    except OSError:
        return []


def _fix_refusal(fix: dict) -> str | None:
    """Why the merger's `--fix` files may not land without a PASS, or None: every path relative, inside the repo
    and on a housekeeping path, never a file agents read as instructions, every file text, and not too much of it."""
    if not isinstance(fix, dict) or not fix:
        return "--fix sent no files"
    allowed = housekeeping_paths()
    total = 0
    for path, text in fix.items():
        if not isinstance(path, str) or not isinstance(text, str):
            return "--fix sends paths and their text"
        why = housekeeping.refusal(path, allowed)
        if why:
            return why
        total += len(text.encode("utf-8"))
    if total > FIX_MAX_BYTES:
        return f"--fix sends {total} bytes; housekeeping is at most {FIX_MAX_BYTES}"
    return None


def request(con, sha: str, message: str, tree: str | None, caller: str | None, fix: dict | None = None,
            squash: bool = False) -> tuple[bool, str]:
    """Queue `sha` to be landed with `message`, the merger having tested a merge whose tree is `tree` - with `fix`,
    its housekeeping files written over the merge, and with `squash`, as one commit carrying `message`."""
    if not is_merger(caller):
        return False, f"{caller or 'someone else'} may not land a branch; the merger does"
    message = (message or "").strip()
    if not message or not (tree or "").strip():
        return False, "a land needs the merge message and the tree the test saw: use `hl land --check <sha>`, then `hl land <sha> <message> --tree <tree>`"
    refusal = _message_refusal(message)
    if refusal:
        return False, f"the merge message carries personal data or a local id: {refusal}"
    if fix is not None:
        why = _fix_refusal(fix)
        if why:
            return False, why
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
    con.execute("BEGIN IMMEDIATE")      # the land and its fix are one write: the sweep never sees one without the other
    try:
        cur = con.execute("INSERT INTO lands(sha, message, tree, requester, state, ts) VALUES(?, ?, ?, ?, 'queued', ?)",
                          (full, message, tree.strip(), caller, int(time.time())))
        if fix is not None or squash:
            con.execute("INSERT INTO land_fixes(land_id, files, squash) VALUES(?, ?, ?)",
                        (cur.lastrowid, json.dumps(fix or {}), int(bool(squash))))
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    ahead = con.execute("SELECT COUNT(*) FROM lands WHERE state IN ('queued', 'running') AND id < ?",
                        (cur.lastrowid,)).fetchone()[0]
    return True, f"land #{cur.lastrowid}: {full[:8]} queued" + (f", {ahead} ahead of it" if ahead else "")


def _tell(con, to: str, body: str, log) -> None:
    rid = db.add_request(con, REQUESTER, to, body, None, None, "fire_and_forget", "say", 0, None)
    db.mark_send_now(con, rid)
    askmod.deliver(rid, REQUESTER, to, body, None,
                   team.Decision(True, "land", kind="say", response="fire_and_forget", chain_depth=0))
    askmod.push(con, to, rid)


def _land(row, fix: dict | None = None, squash: bool = False) -> tuple[bool, str]:
    """Merge `row.sha` into main in a scratch worktree, git only, write the merger's housekeeping `fix` over it
    (a conflict may stand only where `fix` has the file), and fast-forward main when the result is the tree that
    was tested - as the merge, or with `squash` as one commit on main carrying the message. (landed, sha or why not)."""
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
        clean = _git("merge", "--no-ff", "-q", "-m", row["message"], row["sha"], cwd=work)[0] == 0
        if not clean:
            _, files, _ = _git("diff", "--name-only", "--diff-filter=U", cwd=work)
            unfixed = [p for p in files.split() if p not in (fix or {})]
            if unfixed:
                return False, f"conflicts with main in: {' '.join(unfixed)}"
        if fix:
            why = _fix_refusal(fix)         # against main's list as it is now, not as it was when the land was queued
            if why:
                return False, why
            why = housekeeping.write(work, {path: text.encode("utf-8") for path, text in fix.items()})
            if why:
                return False, why
            # Into the merge commit itself: amended when the merge went through, the merge's own commit when it stopped.
            commit = ["--amend", "--no-edit"] if clean else ["-m", row["message"]]
            if _git("add", "--", *fix, cwd=work)[0] != 0 or \
                    _git("commit", "-q", "--no-verify", *commit, cwd=work)[0] != 0:
                return False, "the housekeeping fix could not be committed over the merge"
        _, tree, _ = _git("rev-parse", "HEAD^{tree}", cwd=work)
        if tree.strip() != row["tree"]:
            return False, "main moved since the merger's test: the merge is not the tree it tested; test again"
        _, merged, _ = _git("rev-parse", "HEAD", cwd=work)
        if squash:
            rc, merged, _ = _git("commit-tree", tree.strip(), "-p", "HEAD^1", "-m", row["message"], cwd=work)
            if rc != 0:
                return False, "the squashed commit could not be made"
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
        extra = con.execute("SELECT files, squash FROM land_fixes WHERE land_id = ?", (row["id"],)).fetchone()
        fix = (json.loads(extra["files"]) or None) if extra else None
        landed, out = _land(row, fix, bool(extra and extra["squash"]))
        con.execute("UPDATE lands SET state = ?, merged = ?, output = ?, done_at = ? WHERE id = ?",
                    ("landed" if landed else "failed", out if landed else None, out[-OUTPUT_KEPT:],
                     int(time.time()), row["id"]))
        short = row["sha"][:8]
        body = (f"land #{row['id']}: {short} is on main as {out[:8]}." if landed else
                f"land #{row['id']}: {short} did not land. {textsafe.oneline(out)} "
                f"Test again and send it with `hl land`.")
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
