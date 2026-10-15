#!/usr/bin/env python3
"""Does each repo's normal work still run in the sandbox, and is the founder key out of reach? One real agent per repo.

  HANDLINE_LIVE=1 tools/live_sandbox.py [--only NAME ...] [--go-launch] [--keep]

Spends real tokens (haiku): run it on purpose only. Like tools/live_e2e.py --terminal it starts a test daemon on a scratch
home under a temp test root, in a tmux server of its own, and never touches the live daemon. The scratch home has the
founder switch sandbox_default on, the real shared settings, and one terminal agent per repo below whose agent.yaml says
nothing about the sandbox, so each runs sandboxed exactly as a live agent would after the switch. Each agent's repo is the
real repository and its worktree a temporary detached one of it, as a live agent's is, so its .git allowance is the real
one. The pane's launch is the Python one unless --go-launch.

One agent at a time: it is started, told to run its repo's probe script, and stopped. The script starts its work in the
background (still inside the sandbox) and returns, so no Bash time limit cuts a long build; every step's exit code goes
to probe.log in the worktree, its output to its own file beside it. Each script reads the founder key (must fail), writes
a probe file into a shared build cache (must fail), runs the repo's build and tests (must pass) and makes a git commit in
the worktree (must pass). The table at the end is per repo; a FAIL row names the log to read.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_account  # noqa: E402
import live_e2e as le  # noqa: E402

WS = Path.home() / "workspaces"
# name -> (repository, steps that must pass, run from the worktree). Steps are shell; each is logged under its label.
REPOS = {
    "orcapipe": (WS / "orcapipe", [("go build and test", "cd go && go build ./... && go test ./internal/turns/"),
                                   ("pnpm install", "pnpm install --frozen-lockfile"),
                                   ("app-data tests", "pnpm --dir packages/app-data test")]),
    "harbor": (WS / "harbor-app", [("xcodebuild logic tests", "xcodebuild test -project apps/ios/Harbor.xcodeproj "
                                                              "-scheme HarborLogicTests -destination platform=macOS")]),
    "replaceit": (WS / "replaceit-app", []),
    "legacy": (WS / "@legacy" / "caricatart-web", [("pnpm install", "pnpm install --frozen-lockfile"),
                                                    ("typecheck", "pnpm run typecheck")]),
    "personal": (WS / "@personal", []),
}
STEP_S = 1800      # how long one agent's script may run


def probe_script(wt: Path, key: Path, steps: list[tuple[str, str]]) -> str:
    log = wt / "probe.log"
    cache = subprocess.run(["go", "env", "GOCACHE"], capture_output=True, text=True).stdout.strip()

    def step(label: str, cmd: str) -> str:
        out = wt / f"probe-{label.replace(' ', '-')}.out"
        return f"( cd {wt} && {cmd} ) > {out} 2>&1; echo \"{label}|$?\" >> {log}"
    body = [f"env | grep -E '^(GOCACHE|GOMODCACHE|npm_config_)' | sort > {wt / 'probe-env.out'}",
            f"cat {key} > /dev/null 2>&1; echo \"founder key read|$?\" >> {log}",
            f"echo x > {cache}/zz-handline-sandbox-probe 2>/dev/null; echo \"shared go cache write|$?\" >> {log}",
            *(step(label, cmd) for label, cmd in steps),
            step("git commit", "git add -A -- ':!probe*' && git -c user.name=probe -c user.email=user@example.com "
                               "commit -q --allow-empty -m 'sandbox probe'"),
            f"echo END >> {log}"]
    return "( " + " ; ".join(body) + " ) </dev/null >/dev/null 2>&1 &\n"


def setup(root: Path, account: dict, names: list[str], go_launch: bool) -> tuple[Path, str, Path]:
    """The scratch home (live_e2e.scratch, one agent per repo, the switch on), its founder key and the key's file."""
    home, key = le.scratch(root, account, terminal=True)
    store = next((root / ".config" / "orcapipe" / "protected").iterdir())
    if not go_launch:
        (store / "switches" / "go_launch").unlink()
    (store / "switches" / "sandbox_default").write_text("on")
    subprocess.run(["git", "worktree", "remove", "--force", str(root / "work")], cwd=le.REPO, capture_output=True)
    shutil.rmtree(home / "agents" / le.AGENT)
    team = "agents:\n" + "".join(f"  sbx.{n}: {{}}\n" for n in names)
    (home / "team.yaml").write_text(team + 'edges:\n  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }\n')
    for n in names:
        (home / "agents" / f"sbx.{n}").mkdir(parents=True)
        (home / "agents" / f"sbx.{n}" / "agent.yaml").write_text(f"model: haiku\nmode: default\nrepo: {REPOS[n][0]}\n")
    return home, key, store / "founder-keys" / "key"


def worktree(root: Path, name: str) -> Path:
    wt = root / f"wt-{name}"
    subprocess.run(["git", "worktree", "add", "-q", "--detach", str(wt), "HEAD"], cwd=REPOS[name][0], check=True)
    return wt


def judge(log: Path, steps: list[tuple[str, str]]) -> list[tuple[str, str, str]]:
    """(check, result, note) for one repo."""
    got = dict(line.split("|", 1) for line in log.read_text().splitlines() if "|" in line) if log.exists() else {}
    rows = [("founder key read", "pass" if got.get("founder key read", "0") != "0" else "FAIL", f"rc {got.get('founder key read')}"),
            ("shared go cache write", "pass" if got.get("shared go cache write", "0") != "0" else "FAIL",
             f"rc {got.get('shared go cache write')}")]
    for label in [s[0] for s in steps] + ["git commit"]:
        rc = got.get(label)
        rows.append((label, "pass" if rc == "0" else "FAIL", f"rc {rc}" + ("" if rc == "0" else f"; see {log.parent}/probe-{label.replace(' ', '-')}.out")))
    if "END" not in (log.read_text() if log.exists() else ""):
        rows.append(("script finished", "FAIL", f"no END in {log}"))
    return rows


def run_one(d: le.Daemon, s: le.Stream, home: Path, root: Path, founder: str, key: Path, name: str) -> list[tuple[str, str, str]]:
    agent = f"sbx.{name}"
    wt = worktree(root, name)
    con = sqlite3.connect(home / "db.sqlite", timeout=10)
    con.execute("INSERT OR REPLACE INTO agent_state(agent, state, updated, worktree) VALUES(?, 'queued', 0, ?)", (agent, str(wt)))
    con.commit()
    con.close()
    le.Answerer(home / "daemon.sock", founder, s, agent)
    script = root / f"probe-{name}.sh"
    script.write_text(probe_script(wt, key, REPOS[name][1]))
    t = time.time()
    d.write("session.start", {"agent": agent})
    if not s.wait(t, le.agent_changed(agent, "idle"), t + le.MODEL_S):
        return [("came up idle", "FAIL", "the agent never came up idle")]
    t = time.time()
    d.write("chat.send", {"agent": agent, "body": f"This is a sandbox test in a scratch worktree made for it. Run exactly this one "
                                                   f"command with the Bash tool, once, and do not retry or change anything: "
                                                   f"sh {script}   It starts work in the background and returns at once. "
                                                   f"Then reply with the word done."})
    s.wait(t, le.agent_changed(agent, "idle"), t + le.MODEL_S)
    log, until = wt / "probe.log", time.time() + STEP_S
    while time.time() < until and "END" not in (log.read_text() if log.exists() else ""):
        time.sleep(5)
    rows = judge(log, REPOS[name][1])
    stray = Path(subprocess.run(["go", "env", "GOCACHE"], capture_output=True, text=True).stdout.strip()) / "zz-handline-sandbox-probe"
    stray.unlink(missing_ok=True)
    env = (wt / "probe-env.out").read_text().strip().replace("\n", ", ") if (wt / "probe-env.out").exists() else "none"
    rows.append(("cache env", "info", env))
    t = time.time()
    try:
        d.write("session.stop", {"agent": agent, "mode": "exit"})
        s.wait(t, le.agent_changed(agent, "no_terminal"), t + 60)
    except le.Refused as e:
        rows.append(("stopped", "info", str(e)))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", nargs="*", choices=sorted(REPOS), help="these repos alone")
    ap.add_argument("--go-launch", action="store_true", help="the pane's launch is the Go hl's")
    ap.add_argument("--keep", action="store_true", help="keep the test root and the worktrees")
    args = ap.parse_args()
    if os.environ.get("HANDLINE_LIVE") != "1":
        print("live-sandbox: spends real tokens; HANDLINE_LIVE=1 runs it", file=sys.stderr)
        return 2
    names = args.only or list(REPOS)
    account = live_account.pick_live()
    print(f"live-sandbox: account {account['account']}: {account['why']}", flush=True)
    root = Path(tempfile.mkdtemp(prefix="live-sandbox.")).resolve()
    tmux = f"live-sandbox-{os.getpid()}"
    daemon, table = None, {}
    try:
        binary = str(root / "handlined")
        subprocess.run(["go", "build", "-o", binary, "./cmd/handlined"], cwd=le.REPO / "go", check=True)
        home, key, key_file = setup(root, account, names, args.go_launch)
        daemon = le.start_daemon(binary, root, home, account, tmux)
        d = le.Daemon(home / "daemon.sock", key)
        s = le.Stream(home / "daemon.sock", key)
        print(f"live-sandbox: test daemon {d.hello.get('daemon')}, home {home}", flush=True)
        for name in names:
            print(f"live-sandbox: {name} ...", flush=True)
            table[name] = run_one(d, s, home, root, key, key_file, name)
            for check, result, note in table[name]:
                print(f"  {result:5} {name:10} {check:24} {note}", flush=True)
    except (le.Refused, OSError, subprocess.CalledProcessError, KeyboardInterrupt) as e:
        print(f"live-sandbox: {str(e) or 'interrupted'}", file=sys.stderr)
        return 2
    finally:
        if daemon:
            le.stop_daemon(daemon)
        if args.keep:
            print(f"live-sandbox: kept {root} and the tmux server (tmux -L {tmux})", file=sys.stderr)
        else:
            for name in names:
                subprocess.run(["git", "worktree", "remove", "--force", str(root / f"wt-{name}")], cwd=REPOS[name][0], capture_output=True)
            subprocess.run(["tmux", "-L", tmux, "kill-server"], capture_output=True)
            shutil.rmtree(root, ignore_errors=True)
    return 1 if any(r == "FAIL" for rows in table.values() for _, r, _ in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
