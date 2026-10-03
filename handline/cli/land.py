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


def _tested_tree(sha: str) -> str | None:
    """The tree of the merge `land.sh --check` tested, run here, in the caller's own sandbox."""
    r = subprocess.run([str(paths.repo_root() / "tools" / "land.sh"), "--check", sha], capture_output=True,
                       text=True, cwd=paths.repo_root())
    sys.stderr.write(r.stderr[-2000:])
    out = r.stdout.strip().splitlines()
    return out[-1] if r.returncode == 0 and out else None


def cmd_land(args) -> int:
    if args.list:
        return _list()
    if not args.sha:
        print("usage: hl land --check <sha> | hl land <sha> <merge message> --tree <tree> | hl land --pass <sha> | hl land --list")
        return 1
    if args.check:
        tree = _tested_tree(args.sha)
        if tree is None:
            print("the merge did not test green here; nothing to record")
            return 1
        print(tree)
        return 0
    if args.passed:
        return _send({"verb": "land_pass", "sha": args.sha})
    if not args.message or not args.tree:
        print("usage: hl land --check <sha>, then hl land <sha> <merge message> --tree <tree it printed>")
        return 1
    return _send({"verb": "land", "sha": args.sha, "message": args.message, "tree": args.tree})
