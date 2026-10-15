#!/usr/bin/env python3
"""Does each repo's normal work still run in the sandbox, and is the founder key out of reach? One real agent per repo.

  HANDLINE_LIVE=1 tools/live_sandbox.py [--only NAME ...] [--keep]

Spends real tokens (haiku): run it on purpose only. Like tools/live_e2e.py --terminal it starts a test daemon on a scratch
home under a temp test root, in a tmux server of its own, and never touches the live daemon. The scratch home has the
founder switch sandbox_default on, the real shared settings, and one terminal agent per repo below whose agent.yaml says
nothing about the sandbox, so each runs sandboxed exactly as a live agent would after the switch. Each agent's repo is the
real repository and its worktree a temporary detached one of it, as a live agent's is, so its .git allowance is the real
one. The pane's launch is the Go hl's: only it takes the real account's login under a test root (Python's settings
match it: tests/fixtures/claude_driver.json).

One agent at a time: it is started on the test daemon and, once its CLI is up, the sandbox that CLI runs in is read
off it (the shared settings and its own --settings, whose lists the CLI adds together) and its pane is closed. The repo's
work then runs under that sandbox through the sandbox runtime (srt), with the session's env: the founder key read and a
write into a shared build cache must fail; the repo's build and tests and a git commit in the worktree must pass. No
model is asked to do any of it (a model rightly declines to read a key, and an agent's hl cannot reach a test daemon
through the sandbox, so it cannot read what it is told). Each step's output is in <root>/<repo>-<step>.out.
srt is not the CLI's own sandbox: it has no enableWeakerNetworkIsolation, so a Go module download fails here that a session
makes (tools/probe_sandbox_caches.py and claude -p are the check for that).
"""
from __future__ import annotations

import argparse
import json
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
import tmuxsock  # noqa: E402

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
STEP_S = 1800      # how long one step may run
SRT = "0.0.79"     # the sandbox runtime


def cli_settings(tmux: list[str], handle: str) -> dict | None:
    """The --settings the agent's CLI was started with, read off its command line."""
    shell = subprocess.run([*tmux, "display", "-p", "-t", handle, "#{pane_pid}"], capture_output=True, text=True).stdout.strip()
    kids = subprocess.run(["pgrep", "-P", shell], capture_output=True, text=True).stdout.split()
    args = subprocess.run(["ps", "-ww", "-o", "args=", "-p", kids[0]], capture_output=True, text=True).stdout if kids else ""
    try:
        return json.loads(args[args.index("--settings ") + len("--settings "):].split(" --", 1)[0])
    except ValueError:
        return None


def sandbox_of(shared: dict, own: dict, wt: Path) -> dict:
    """The sandbox the session runs in: the CLI adds the lists of its settings sources together, and its working directory
    is writable. As srt's config."""
    fs, net = {}, {}
    for src in (shared.get("sandbox", {}), own.get("sandbox", {})):
        for k, v in src.get("filesystem", {}).items():
            fs[k] = fs.get(k, []) + v
        for k, v in src.get("network", {}).items():
            net[k] = net.get(k, []) + v if isinstance(v, list) else v
    fs["allowWrite"] = [str(wt), *fs.get("allowWrite", [])]
    net.setdefault("deniedDomains", [])     # srt refuses a config without it
    return {"filesystem": fs, "network": net}


def setup(root: Path, account: dict, names: list[str]) -> tuple[Path, str, Path]:
    """The scratch home (live_e2e.scratch, one agent per repo, the switch on), its founder key and the key's file."""
    home, key = le.scratch(root, account, terminal=True)
    store = next((root / ".config" / "orcapipe" / "protected").iterdir())
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



def run_one(d: le.Daemon, s: le.Stream, sock: str, home: Path, root: Path, key: Path, name: str) -> list[tuple[str, str, str]]:
    agent = f"sbx.{name}"
    wt = worktree(root, name)
    con = sqlite3.connect(home / "db.sqlite", timeout=10)
    con.execute("INSERT OR REPLACE INTO agent_state(agent, state, updated, worktree) VALUES(?, 'queued', 0, ?)", (agent, str(wt)))
    con.commit()
    t = time.time()
    try:
        d.write("session.start", {"agent": agent})
    except le.Refused as e:   # a session held at a dialog is refused after 20 s; the dialog is answered below
        print(f"  ({e})".splitlines()[0], flush=True)
    tmux = ["tmux", "-L", sock]
    if not s.wait(t, le.agent_changed(agent, "idle"), t + 30):
        # a scratch worktree is a folder no account trusts yet, unlike a live agent's: the CLI asks first
        handle = con.execute("SELECT terminal FROM agent_state WHERE agent = ?", (agent,)).fetchone()[0]
        screen = subprocess.run([*tmux, "capture-pane", "-p", "-t", handle], capture_output=True, text=True).stdout
        if "Yes, I trust this folder" in screen:
            subprocess.run([*tmux, "send-keys", "-t", handle, "Down", "Enter"], check=True)
        if not s.wait(t, le.agent_changed(agent, "idle"), t + le.MODEL_S):
            return [("came up idle", "FAIL", "the agent never came up idle")]
    handle = con.execute("SELECT terminal FROM agent_state WHERE agent = ?", (agent,)).fetchone()[0]
    con.close()
    own = cli_settings(tmux, handle)
    subprocess.run([*tmux, "kill-pane", "-t", handle], capture_output=True)    # the next agent runs alone
    if own is None:
        return [("session settings", "FAIL", "could not read --settings off the CLI")]
    shared = json.loads((home / "shared" / ".claude" / "settings.json").read_text())
    conf = root / f"srt-{name}.json"
    conf.write_text(json.dumps(sandbox_of(shared, own, wt)))
    env = {**os.environ, **own.get("env", {})}
    srt = ["npx", "-y", f"@anthropic-ai/sandbox-runtime@{SRT}", "--settings", str(conf), "-c"]

    Path("/tmp/claude").mkdir(exist_ok=True)     # srt's TMPDIR, which it does not make (the CLI makes its own)
    excluded = [p.removesuffix(" *") for p in shared.get("sandbox", {}).get("excludedCommands", [])]

    def run(cmd: str, label: str, timeout: int = STEP_S) -> int:
        out = wt.parent / f"{name}-{label.replace(' ', '-')}.out"
        # the CLI runs a command its settings exclude outside the sandbox, so this does too
        argv = ["sh", "-c", cmd] if cmd.split()[0] in excluded else [*srt, cmd]
        try:
            with out.open("w") as f:
                return subprocess.run(argv, cwd=wt, env=env, stdout=f, stderr=subprocess.STDOUT, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            return -1

    sb = own.get("sandbox", {})
    gocache = own.get("env", {}).get("GOCACHE", "none")
    rows = [("sandbox on in the session", "pass" if sb.get("enabled") is True else "FAIL", f"enabled {sb.get('enabled')}"),
            ("own caches in its env", "pass" if gocache.startswith(str(home / "caches" / agent)) else "FAIL", gocache)]
    rc = run(f"echo x > {wt}/srt-control", "worktree write", 60)
    rows.append(("worktree write (control)", "pass" if rc == 0 else "FAIL", f"rc {rc}" + ("" if rc == 0 else ": srt did not run, the rows below say nothing")))
    if not os.access(key, os.R_OK):   # else a missing key would read as refused
        return rows + [("founder key read", "FAIL", f"{key} is not readable outside the sandbox: nothing to refuse")]
    rc = run(f"cat {key}", "founder key read", 60)
    rows.append(("founder key read", "pass" if rc not in (0, -1) else "FAIL", f"rc {rc}"))
    probe = Path(subprocess.run(["go", "env", "GOCACHE"], capture_output=True, text=True).stdout.strip()) / "zz-handline-sandbox-probe"
    rc = run(f"echo x > {probe}", "shared go cache write", 60)
    rows.append(("shared go cache write", "pass" if not probe.exists() else "FAIL", f"rc {rc}"))
    probe.unlink(missing_ok=True)
    commit = "rm -f srt-control && git -c user.name=probe -c user.email=user@example.com commit -q --allow-empty -m 'sandbox probe'"
    for label, cmd in [*REPOS[name][1], ("git commit", commit)]:
        t = time.time()
        rc = run(cmd, label)
        note = f"rc {rc}, {time.time() - t:.0f}s" + (" (excluded: runs unsandboxed)" if cmd.split()[0] in excluded else "")
        rows.append((label, "pass" if rc == 0 else "FAIL", note if rc == 0 else f"{note}; see {root}/{name}-{label.replace(' ', '-')}.out"))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", nargs="*", choices=sorted(REPOS), help="these repos alone")
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
        home, key, key_file = setup(root, account, names)
        daemon = le.start_daemon(binary, root, home, account, tmux)
        d = le.Daemon(home / "daemon.sock", key)
        s = le.Stream(home / "daemon.sock", key)
        print(f"live-sandbox: test daemon {d.hello.get('daemon')}, home {home}", flush=True)
        for name in names:
            print(f"live-sandbox: {name} ...", flush=True)
            table[name] = run_one(d, s, tmux, home, root, key_file, name)
            for check, result, note in table[name]:
                print(f"  {result:5} {name:10} {check:26} {note}", flush=True)
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
            tmuxsock.stop(tmux)
            shutil.rmtree(root, ignore_errors=True)
    return 1 if any(r == "FAIL" for rows in table.values() for _, r, _ in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
