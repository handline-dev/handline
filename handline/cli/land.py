"""`hl land` - the merger tests a branch in its own sandbox (`--check`) and records it (`--tree`); the auditor records a PASS;
the daemon, which runs no branch code, merges and fast-forwards main."""
from __future__ import annotations

import subprocess
import sys

from .. import db
from ..daemon import client as dmn_client
from ..infra import paths
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


def _tested_trees(shas: list[str]) -> list[str] | None:
    """The tree after each merge `land.sh --check` tested, in order, run here, in the caller's own sandbox."""
    r = subprocess.run([str(paths.repo_root() / "tools" / "land.sh"), "--check", *shas], capture_output=True,
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
        if len(args.check) == 1:
            trees = _tested_trees(args.check)
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
        return _send({"verb": "land_pass", "sha": args.sha})
    if not args.message or not args.tree:
        print("usage: hl land --check <sha>, then hl land <sha> <merge message> --tree <tree it printed>")
        return 1
    return _send({"verb": "land", "sha": args.sha, "message": args.message, "tree": args.tree})
