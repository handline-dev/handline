#!/usr/bin/env python3
"""Can a sandboxed session write what unsandboxed processes build or run from? Ask a sandboxed claude, in a scratch repo.

  tools/probe_sandbox_caches.py     one line per attempt, then HOLDS or GAP (exit 0, 1; 2 if inconclusive)

Run it from your own terminal, not from an agent session whose Bash is sandboxed. It builds a repository and a linked
worktree under a temp folder and launches `claude -p` in the worktree with the settings a sandboxed session gets
(vendors/claude.py launch_argv with the repo's .git and the session's own cache folder). The sandboxed command then:

  refused: a file in each shared cache an unsandboxed build reads (the Go build and module caches, npm's cache and the
           pnpm store, found from this terminal), the repo's .git/hooks, and its .git/config
  allowed: a Go build and test of a small module (the caches must be the session's own) and a git commit in the worktree

A probe file that does get written is removed afterwards. The temp folder is kept and printed.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.probe_main_deny import git  # noqa: E402

PROBE = "zz-handline-sandbox-probe"


def shared_caches() -> dict[str, Path]:
    """The caches this (unsandboxed) terminal's builds use, by name."""
    def out(*cmd: str) -> str:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""
    found = {"go build cache": out("go", "env", "GOCACHE"), "go module cache": out("go", "env", "GOMODCACHE"),
             "npm cache": out("npm", "config", "get", "cache"), "pnpm store": out("pnpm", "store", "path")}
    return {k: Path(v) for k, v in found.items() if v and Path(v).is_dir()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--claude", default=shutil.which("claude"), help="the claude executable")
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args(argv)
    if not (args.claude and shutil.which(args.claude)):
        print("claude not found; pass --claude", file=sys.stderr)
        return 2
    from handline.vendors import claude

    top = Path(tempfile.mkdtemp(prefix="probe-sandbox-caches-")).resolve()
    repo, wt, own = top / "repo", top / "wt", top / "caches" / "agent"
    repo.mkdir()
    own.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "commit", "-q", "--allow-empty", "-m", "start")
    git(repo, "worktree", "add", "-q", "-b", "work", str(wt))
    (wt / "go.mod").write_text("module probe\n\ngo 1.21\n")
    (wt / "p.go").write_text("package probe\n\nfunc Two() int { return 2 }\n")
    (wt / "p_test.go").write_text('package probe\n\nimport "testing"\n\nfunc TestTwo(t *testing.T) {\n\tif Two() != 2 {\n\t\tt.Fatal()\n\t}\n}\n')
    G = repo / ".git"
    refused = {**{name: path / PROBE for name, path in shared_caches().items()},
               ".git/hooks": G / "hooks" / PROBE, ".git/config": G / "config"}
    log = wt / "probe.log"  # the worktree is the one place outside the allowances the session may write
    lines = [f"cd {wt}"]
    for name, path in refused.items():
        write = f"echo '[{PROBE}]' >> {path}" if name == ".git/config" else f"echo x > {path}"
        lines.append(f"{write} 2>/dev/null; echo 'refuse|{name}|'$? >> {log}")
    lines += [f"go env GOCACHE GOMODCACHE >> {log}",
              f"go build ./... && go test ./... >/dev/null; echo 'allow|go build and test|'$? >> {log}",
              f"git add -A && git -c user.name=probe -c user.email=user@example.com commit -qm probe; echo 'allow|git commit|'$? >> {log}"]
    (top / "probe.sh").write_text("\n".join(lines) + "\n")
    argv_ = claude.Claude().launch_argv("claude", None, "low", "", [], None, sandbox=True,
                                        allow_write=[str(G)], cache_dir=str(own))
    settings = json.loads(argv_[argv_.index("--settings") + 1])
    settings["sandbox"]["allowUnsandboxedCommands"] = False
    prompt = (f"This is a test of a sandbox, in a scratch repository made for it. Run exactly this one command with the "
              f"Bash tool, once, and do not retry or change anything if a part of it fails: sh {top / 'probe.sh'}   "
              f"Then reply with the word done.")
    print(f"scratch: {top}")
    try:
        r = subprocess.run([args.claude, "-p", prompt, "--settings", json.dumps(settings), "--model", "haiku",
                            "--dangerously-skip-permissions", "--no-chrome"], cwd=wt, capture_output=True, text=True,
                           timeout=args.timeout)
        said = (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        said = f"(claude timed out after {args.timeout}s)"

    got = {}
    for line in log.read_text().splitlines() if log.exists() else []:
        print("  " + line)
        if line.count("|") == 2:
            kind, name, rc = line.split("|")
            got[name] = (kind, rc)
    gaps, missing = [], []
    for name, path in refused.items():
        wrote = PROBE in path.read_text() if name == ".git/config" else path.exists()
        if name not in got:
            missing.append(name)
        print(f"{name:20} {'WROTE' if wrote else 'refused'}")
        if wrote:
            gaps.append(name)
            if name != ".git/config":
                path.unlink()
    for name in ("go build and test", "git commit"):
        rc = got.get(name, (None, None))[1]
        print(f"{name:20} {'ok' if rc == '0' else f'FAILED rc={rc}'}")
        if rc != "0":
            gaps.append(name + " failed")
    print(f"claude said: {said[-300:]}")
    if missing:
        print("INCONCLUSIVE: never ran: " + ", ".join(missing))
        return 2
    print("GAP: " + ", ".join(gaps) if gaps else "HOLDS")
    return 1 if gaps else 0


if __name__ == "__main__":
    sys.exit(main())
