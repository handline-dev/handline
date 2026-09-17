"""ocp doctor - check the preconditions each phase rests on (KICKOFF rule 6).

No phase is finished without it. Every check is read-only, and credentials are
only ever checked for existence.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import accounts, agents, hooks, paths, status

OK, WARN, FAIL = "ok", "warn", "fail"

# If managed settings set either of these, the collector dies silently
# (section 4 constraint).
HOOK_KILLERS = ("disableAllHooks", "allowManagedHooksOnly")
MANAGED_PATHS = [
    Path("/Library/Application Support/ClaudeCode/managed-settings.json"),
    Path("/etc/claude-code/managed-settings.json"),
    Path.home() / ".claude" / "managed-settings.json",
]


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, level: str, name: str, detail: str = "") -> None:
        self.rows.append((level, name, detail))

    @property
    def failed(self) -> int:
        return sum(1 for lv, _, _ in self.rows if lv == FAIL)

    @property
    def warned(self) -> int:
        return sum(1 for lv, _, _ in self.rows if lv == WARN)

    def render(self, color: bool = True) -> str:
        if color:
            mark = {OK: "\033[32m+\033[0m", WARN: "\033[33m!\033[0m", FAIL: "\033[31mx\033[0m"}
        else:
            mark = {OK: "+", WARN: "!", FAIL: "x"}
        lines = [f"{mark[lv]} {name}" + (f"\n    {d}" if d else "") for lv, name, d in self.rows]
        lines += ["", f"{len(self.rows)} checks, {self.failed} failed, {self.warned} warnings"]
        return "\n".join(lines)


def _check_tools(r: Report) -> None:
    for tool, why, hard in (
        ("claude", "needed to run agents", True),
        ("jq", "the collector uses it", True),
        ("orca", "terminal and worktree control", False),
    ):
        found = shutil.which(tool)
        if found:
            r.add(OK, f"{tool} found", found)
        else:
            r.add(FAIL if hard else WARN, f"{tool} missing", why)


def _check_managed(r: Report) -> None:
    found = False
    for p in MANAGED_PATHS:
        if not p.exists():
            continue
        found = True
        try:
            data = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError) as e:
            r.add(WARN, f"managed-settings unreadable: {p}", str(e))
            continue
        hit = [k for k in HOOK_KILLERS if data.get(k)]
        if hit:
            r.add(FAIL, "managed settings disable hooks/statusLine", f"{p}: {', '.join(hit)}")
        else:
            r.add(OK, f"managed settings harmless: {p.name}")
    if not found:
        r.add(OK, "no managed-settings.json", "checked all three standard locations")


def _check_home(r: Report) -> None:
    h = paths.home()
    if not h.is_dir():
        r.add(FAIL, f"OCP_HOME missing: {h}", "run ocp init first")
        return
    r.add(OK, f"OCP_HOME {h}")

    c = paths.collector()
    src = paths.repo_root() / "libexec" / "statusline-collect.sh"
    if not c.exists():
        r.add(FAIL, "collector not installed", str(c))
    elif not os.access(c, os.X_OK):
        r.add(FAIL, "collector is not executable", str(c))
    elif src.exists() and src.read_bytes() != c.read_bytes():
        # The installed copy is what actually runs. Editing the repo copy and
        # forgetting to reinstall means fixes silently never take effect.
        r.add(FAIL, "installed collector differs from the repo copy",
              f"{c} is stale - run `ocp init` to reinstall")
    else:
        r.add(OK, "collector installed and current", str(c))

    s = paths.shared_claude() / "settings.json"
    if not s.exists():
        r.add(FAIL, "shared/.claude/settings.json missing")
        return
    try:
        line = json.loads(s.read_text()).get("statusLine", {})
    except json.JSONDecodeError:
        line = {}
    if line.get("command") == str(c):
        r.add(OK, "shared settings.json points at our collector")
    else:
        r.add(FAIL, "shared statusLine is not our collector", f"currently {line.get('command')!r}")


def _check_orca_hooks(r: Report) -> None:
    src, dst, drift = hooks.plan()
    if not src:
        r.add(WARN, "no Orca hooks in ~/.claude/settings.json",
              "Orca has never run here, or its integration is off")
    elif drift:
        r.add(WARN, "Orca hooks have drifted from shared",
              f"{len(src)} events at source vs {len(dst)} in shared - "
              "run `ocp hooks sync`, or let the watcher do it")
    else:
        r.add(OK, f"Orca hooks in sync ({len(src)} events)")


def _check_accounts(r: Report) -> None:
    names = paths.list_accounts()
    if not names:
        r.add(FAIL, "no accounts registered", "ocp account register <name>")
        return
    for a in names:
        cdir = paths.account_claude(a)
        if not cdir.is_dir():
            r.add(FAIL, f"account {a}: CLAUDE_CONFIG_DIR missing", str(cdir))
            continue
        if accounts.logged_in(a):
            r.add(OK, f"account {a}: logged in")
        else:
            r.add(FAIL, f"account {a}: not logged in",
                  "a human runs this once: " + accounts.login_command(a))

        link = cdir / "settings.json"
        want = paths.shared_claude() / "settings.json"
        if link.is_symlink() and Path(os.readlink(link)) == want:
            r.add(OK, f"account {a}: settings.json -> shared")
        else:
            r.add(FAIL, f"account {a}: settings.json does not point at shared",
                  f"ocp account register --force {a}")

        st = status.read(a)
        allowed, why = st.assignable()
        if not st.exists:
            r.add(WARN, f"account {a}: no status.json",
                  "no session has run yet - assignment refused (fail-closed)")
        elif allowed:
            r.add(OK, f"account {a}: assignable", why)
        else:
            r.add(WARN, f"account {a}: not assignable", why)


def _check_agents(r: Report) -> None:
    names = paths.list_agents()
    if not names:
        r.add(WARN, "no agents registered", "ocp agent register <name> --account <account>")
        return
    known = set(paths.list_accounts())
    for n in names:
        try:
            acct = agents.account_of(n)
        except Exception as e:  # noqa: BLE001 - config errors are reported as-is
            r.add(FAIL, f"agent {n}: agent.yaml error", str(e))
            continue
        if acct in known:
            r.add(OK, f"agent {n} -> account {acct}")
        else:
            r.add(FAIL, f"agent {n}: account '{acct}' is not registered")
        for fn, size, budget, over in agents.budget_report(n):
            if over:
                r.add(WARN, f"agent {n}: {fn} over budget",
                      f"{size}B > {budget}B, and this is per-session overhead")


# statusLine's rate_limits arrived in this version; below it there is no usage
# to read and the whole data source is missing.
MIN_CLAUDE_VERSION = (2, 1, 0)


def _check_claude_version(r: Report) -> None:
    exe = shutil.which("claude")
    if not exe:
        return
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True,
                             timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        r.add(WARN, "could not read the Claude Code version", str(e))
        return
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    if not m:
        r.add(WARN, "could not parse the Claude Code version", out[:80])
        return
    version = tuple(int(x) for x in m.groups())
    shown = ".".join(str(v) for v in version)
    if version < MIN_CLAUDE_VERSION:
        r.add(FAIL, f"Claude Code {shown} is too old",
              f"statusLine rate_limits needs >= "
              f"{'.'.join(str(v) for v in MIN_CLAUDE_VERSION)}")
    else:
        r.add(OK, f"Claude Code {shown}")


def _check_rate_limits_present(r: Report) -> None:
    """The collector can run fine and still deliver no usage, which is the
    fail-open shape section 4 exists to prevent. Check the field, not the file."""
    seen = False
    for acct in paths.list_accounts():
        st = status.read(acct)
        if not st.exists:
            continue
        seen = True
        src = st.pct_5h_source
        if src == "unknown":
            r.add(WARN, f"account {acct}: no usable rate_limits in the last snapshot",
                  "the snapshot is missing, unreadable or too old - assignment stays refused")
        elif src == "quiet":
            r.add(OK, f"account {acct}: 5h window quiet",
                  "no session has spent in the current window; reads as 0%")
        elif src == "carried":
            r.add(OK, f"account {acct}: rate_limits carried", f"5h {st.pct_5h}% (window still open)")
        else:
            r.add(OK, f"account {acct}: rate_limits present", f"5h {st.pct_5h}%")
    if not seen and paths.list_accounts():
        r.add(WARN, "no snapshot has rate_limits yet", "no session has run")


def _check_pidfile(r: Report) -> None:
    from . import daemon

    p = paths.daemon_pid()
    if not p.exists():
        r.add(OK, "no daemon pidfile", "daemon is not running")
        return
    pid = daemon.running_elsewhere()
    if pid:
        r.add(OK, f"daemon running (pid {pid})")
    else:
        r.add(WARN, "stale pidfile was reclaimed",
              "it named a dead process or a previous boot")


def _check_autocontinue(r: Report) -> None:
    """Claude Code's own autoContinueAtUsageLimit overlaps our resume logic
    (section 4). One of them has to own it; doctor keeps the choice explicit."""
    seen = []
    for p in (paths.shared_claude() / "settings.json", Path.home() / ".claude" / "settings.json"):
        if not p.exists():
            continue
        try:
            v = json.loads(p.read_text()).get("autoContinueAtUsageLimit")
        except (OSError, json.JSONDecodeError):
            continue
        if v is not None:
            seen.append((p, v))
    if not seen:
        r.add(WARN, "autoContinueAtUsageLimit is unset",
              "it overlaps our resume path - pin it to false in shared settings")
        return
    for p, v in seen:
        if v:
            r.add(FAIL, "autoContinueAtUsageLimit is on",
                  f"{p}: duplicates our resume logic, pick one")
        else:
            r.add(OK, "autoContinueAtUsageLimit=false", str(p))


def run() -> Report:
    r = Report()
    _check_tools(r)
    _check_claude_version(r)
    _check_managed(r)
    _check_home(r)
    _check_orca_hooks(r)
    _check_accounts(r)
    _check_rate_limits_present(r)
    _check_agents(r)
    _check_pidfile(r)
    _check_autocontinue(r)
    return r
