"""`hl land` - the merger tests a branch in its own sandbox (`--check`) and records it (`--tree`); the auditor records a PASS;
the daemon, which runs no branch code, merges and fast-forwards main."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .. import db
from ..infra import daemonclient as dmn_client, paths, textsafe
from ..team import ask as askmod


def _list() -> int:
    con = db.connect()
    try:
        rows = con.execute("SELECT id, sha, state, merged, requester FROM lands ORDER BY id DESC LIMIT 15").fetchall()
    finally:
        con.close()
    if not rows:
        print("nothing has been recorded to land")
    for r in rows:
        print(f"#{r['id']}  {r['sha'][:8]}  {r['state']}" + (f" as {r['merged'][:8]}" if r["merged"] else "")
              + f"  by {r['requester']}")
    return 0


def _send(payload: dict) -> int:
    me = askmod.current_agent()
    reply = dmn_client.send({**payload, "agent": me})
    if reply is None:
        print("daemon not running, nothing recorded")
        return 1
    print(reply.get("said", ""))
    return 0 if reply.get("ok") else 1


def _passed_flag(sha: str) -> list[str]:
    """`--passed` when the auditor's PASS for `sha` is on record, so land.sh can skip a rerun of the
    suite the auditor already ran on that very tree; anything unreadable means a full run."""
    try:
        full = subprocess.run(["git", "rev-parse", "--verify", f"{sha}^{{commit}}"], capture_output=True,
                              text=True, cwd=paths.repo_root()).stdout.strip()
        con = db.connect()
        try:
            known = con.execute("SELECT 1 FROM land_passes WHERE sha = ?", (full,)).fetchone()
        finally:
            con.close()
    except Exception:  # noqa: BLE001 - no db or no sha: the suite runs
        return []
    return ["--passed"] if known else []


def _refs_refusal(sha: str) -> str:
    """Why `land.sh --check` would refuse `sha` before its suite runs (a conflict with main, or personal data
    or a local id in the merge, by the merged tree's own tools/localrefs.py): the merger's own check, with
    `--refs` to skip the suite. Empty when it would go on to the suite."""
    try:
        r = subprocess.run([str(paths.repo_root() / "tools" / "land.sh"), "--check", "--refs", sha],
                           capture_output=True, text=True, cwd=paths.repo_root(), timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return f"land.sh --check --refs could not run ({type(e).__name__}); try again"
    if r.returncode == 0:
        return ""
    return textsafe.text((r.stdout + r.stderr).strip()) or f"land.sh --check --refs {sha} exited {r.returncode}"


def _tested_trees(shas: list[str], fix: str | None = None) -> list[str] | None:
    """The tree after each merge `land.sh --check` tested, in order, run here, in the caller's own sandbox.
    A single passed sha may skip the suite rerun (`--passed`); a train's merge is never one sha's tree.
    `fix` is the merger's housekeeping directory for a single sha (`land.sh --check --fix`)."""
    flag = _passed_flag(shas[0]) if len(shas) == 1 else []
    if fix:
        flag += ["--fix", fix]
    r = subprocess.run([str(paths.repo_root() / "tools" / "land.sh"), "--check", *flag, *shas], capture_output=True,
                       text=True, cwd=paths.repo_root())
    sys.stderr.write(r.stderr[-2000:])
    out = r.stdout.strip().splitlines()
    return out[-len(shas):] if r.returncode == 0 and len(out) >= len(shas) else None


def train(shas: list[str], check=None) -> tuple[list[tuple[str, str]], list[str]]:
    """Test `shas` merged onto main in order with one check; on red, split the batch in half and check each
    half on top of what already tested green. ((sha, tree) to land in that order, the shas dropped)."""
    check = check or _tested_trees
    kept: list[str] = []
    trees: list[str] = []
    dropped: list[str] = []

    def go(batch: list[str]) -> None:
        nonlocal kept, trees
        if not batch:
            return
        got = check(kept + batch)
        if got is not None:
            kept, trees = kept + batch, got
        elif len(batch) == 1:
            dropped.append(batch[0])
        else:
            go(batch[:len(batch) // 2])
            go(batch[len(batch) // 2:])
    go(list(shas))
    return list(zip(kept, trees)), dropped


def cmd_land(args) -> int:
    if args.list:
        return _list()
    if args.check:
        if args.fix and len(args.check) != 1:
            print("--fix is for one sha, not a train: check and land the others first")
            return 1
        if len(args.check) == 1:
            trees = _tested_trees(args.check, args.fix)
            if trees is None:
                print("the merge did not test green here; nothing to record")
                return 1
            print(trees[0])
            return 0
        kept, dropped = train(args.check)
        for sha, tree in kept:
            print(f"{sha} {tree}")
        for sha in dropped:
            print(f"dropped {sha}: it does not test green on top of the others; send it back to its owner")
        if kept:
            print("land them in this order: `hl land <sha> <merge message> --tree <tree>` for each")
        return 0 if kept else 1
    if not args.sha:
        print("usage: hl land --check <sha>... | hl land <sha> <merge message> --tree <tree> | hl land --pass <sha> | hl land --list")
        return 1
    if args.passed:
        refused = _refs_refusal(args.sha)
        if refused:
            print(refused)
            print(f"no PASS recorded for {args.sha}: the merger's check stops on the above")
            return 1
        return _send({"verb": "land_pass", "sha": args.sha})
    if not args.message or not args.tree:
        print("usage: hl land --check <sha>, then hl land <sha> <merge message> --tree <tree it printed>")
        return 1
    payload = {"verb": "land", "sha": args.sha, "message": args.message, "tree": args.tree}
    if args.squash:
        payload["squash"] = True
    if args.fix:
        files, err = _fix_files(Path(args.fix))
        if err:
            print(err)
            return 1
        payload["fix"] = files
    return _send(payload)


def _fix_files(root: Path) -> tuple[dict[str, str], str]:
    """{repo path: text} for every file under `root`, the merger's `--fix` directory; ({}, why) when it cannot be sent.
    Which paths may land is the daemon's to say (main's tools/housekeeping.paths)."""
    if not root.is_dir():
        return {}, f"--fix {root}: not a directory"
    files = {}
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            return {}, f"--fix {p}: a link; send the file itself"
        if p.is_file():
            try:
                files[p.relative_to(root).as_posix()] = p.read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                return {}, f"--fix {p}: not UTF-8 text; housekeeping is text"
    return (files, "") if files else ({}, f"--fix {root}: no files in it")
