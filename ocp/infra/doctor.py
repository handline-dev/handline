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

from ..team import accounts, agents
from .. import db, vendors
from ..session import launch, observe as obs, orca
from ..usage import load as loadmod, status
from . import paths, service, textsafe
from . import timefmt
from ..daemon import reconciler as dmn_reconciler

OK, WARN, FAIL = "ok", "warn", "fail"

# If managed settings set either of these, our hooks never run - and since
# D11 they are what keeps every agent's context and session identity current
# between daemon ticks, as well as what records every event.
HOOK_KILLERS = ("disableAllHooks", "allowManagedHooksOnly")
MANAGED_PATHS = [
    Path("/Library/Application Support/ClaudeCode/managed-settings.json"),
    Path("/etc/claude-code/managed-settings.json"),
    paths.user_home() / ".claude" / "managed-settings.json",
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


def _vendors_in_use() -> list:
    """The driver of every vendor an account is for, the default's first."""
    names = {agents.DEFAULT_VENDOR, *(vendors.account_vendor(a) for a in paths.list_accounts())}
    out = []
    for n in sorted(names, key=lambda n: (n != agents.DEFAULT_VENDOR, n)):
        try:
            out.append(vendors.get(n))
        except vendors.UnknownVendor:
            pass  # `_check_accounts` names the account that records it
    return out


def _check_tools(r: Report) -> None:
    for tool, found, why, hard in (
        *((drv.name, drv.binary(), "needed to run agents", True) for drv in _vendors_in_use()),
        ("orca", shutil.which("orca"), "terminal and worktree control", False),
    ):
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

    for sh in vendors.state_hooks():
        for what, detail in sh.shared_problems():
            r.add(FAIL, what, detail)


def _check_mcp_config(r: Report) -> None:
    # `launch.mcp_config_arg` is the one place that decides "usable" - calling
    # it here rather than re-checking the file keeps doctor from reporting OK
    # on a shape a launch would still skip.
    p = paths.mcp_config()
    if not p.exists():
        r.add(OK, "no shared mcp.json (optional)", str(p))
        return
    _, err = launch.mcp_config_arg()
    if err:
        r.add(FAIL, "shared mcp.json is invalid", f"{err} - launches skip --mcp-config until fixed")
        return
    r.add(OK, "shared mcp.json is valid", str(p))


def _check_orca_hooks(r: Report) -> None:
    for h in vendors.state_hooks():
        src, dst, drift = h.plan()
        if not src:
            where = "~/" + str(h.source_settings().relative_to(paths.user_home()))
            r.add(WARN, f"no Orca hooks in {where}",
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
        r.add(FAIL, "no accounts registered", "ocp account add <name>")
        return
    loads = loadmod.read_all()
    for a in names:
        try:
            drv = vendors.for_account(a)
        except vendors.UnknownVendor as e:
            r.add(FAIL, f"account {a}: {e}", str(paths.account_dir(a) / "vendor"))
            continue
        cdir = vendors.config_dir(a)
        if not cdir.is_dir():
            r.add(FAIL, f"account {a}: {drv.config_dir_env()} missing", str(cdir))
            continue
        if accounts.logged_in(a):
            r.add(OK, f"account {a}: logged in")
        else:
            r.add(FAIL, f"account {a}: not logged in",
                  "a human runs this once: " + accounts.login_command(a))

        link = cdir / "settings.json"
        want = vendors.shared_dir("claude") / "settings.json"
        if "settings.json" not in drv.shared_items():
            pass
        elif link.is_symlink() and Path(os.readlink(link)) == want:
            r.add(OK, f"account {a}: settings.json -> shared")
        else:
            r.add(FAIL, f"account {a}: settings.json does not point at shared",
                  f"ocp account register --force {a}")

        if not drv.meters():
            r.add(OK, f"account {a}: unmetered ({drv.name})",
                  "never picked or polled; only agents that name it run there")
            continue
        st = status.read(a)
        ld = loads.get(a)
        allowed, why = st.assignable(ld.active if ld else 0)
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
        r.add(WARN, "no agents registered", "ocp agent register <name>")
        return
    have = {vendors.account_vendor(a) for a in paths.list_accounts()}
    for n in names:
        try:
            vendor = vendors.agent_vendor(n)
            vendors.get(vendor)
        except Exception as e:  # noqa: BLE001 - config errors are reported as-is
            r.add(FAIL, f"agent {n}: agent.yaml error", str(e))
            continue
        if vendor not in have:
            r.add(FAIL, f"agent {n}: no {vendor} account is registered",
                  f"its launch is refused; `ocp account add <name> --vendor {vendor}`")
        else:
            r.add(OK, f"agent {n}")
        for fn, size, budget, over in agents.budget_report(n):
            if over:
                r.add(WARN, f"agent {n}: {fn} over budget",
                      f"{size}B > {budget}B, and this is per-session overhead")


def _check_stuck_running(r: Report) -> None:
    """RUNNING is the state that takes an agent out of service.

    Work is not delivered to it and its session is never closed, so an agent
    that entered RUNNING and never left is ignored by the daemon while every
    screen reports it working.

    The sweep repairs this on its own, so this is not a report of something
    nobody is doing anything about - it is the half the sweep declines to do.
    Unsticking rests on a probe, so the sweep wants a second witness before it
    acts, and stays its hand when Orca cannot be reached or says the terminal is
    mid-turn. An agent that stays here across several runs of `doctor` is one
    the sweep has looked at and left, which is either a genuinely long turn or
    something a human needs to see.
    """
    con = db.connect()
    try:
        for row in db.all_agent_states(con):
            mins = dmn_reconciler.silent_minutes(con, row["agent"], row)
            if mins is None:
                continue
            r.add(WARN, f"agent {row['agent']}: RUNNING with nothing heard for {mins}m",
                  "no work is delivered to a RUNNING agent and its session is never "
                  "closed; the sweep unsticks it once Orca agrees it is idle")
    finally:
        con.close()


def _check_launch_drift(r: Report) -> None:
    """A running session whose own launch no longer matches `build()`.

    The daemon relaunches it at its next wake (`daemon._wake_agent`); this is
    only visibility for the window before that. See `launch.fingerprint`.
    """
    sessions = obs.live_sessions()
    if sessions is None:
        return                      # could not look; not evidence of drift
    con = db.connect()
    try:
        for row in db.all_agent_states(con):
            agent = row["agent"]
            if row["closed_at"] or not sessions.get(agent):
                continue
            if launch.drifted(agent, row):
                r.add(WARN, f"agent {agent}: running session predates the current launch "
                      "recipe",
                      "will be relaunched, with its own session to resume, the next time "
                      "it is woken instead of having its handoff typed in place")
    finally:
        con.close()


# A transcript written this long after we recorded the close is not the last
# thing that session wrote on its way out.
WRITTEN_AFTER_CLOSE_S = 60


def _check_session_matches_the_record(r: Report) -> None:
    """An agent whose session is still writing where the process table has none.

    Every path that types into a terminal asks `observe.live_sessions()` first,
    and that answer rests on one string: a session is a process whose command
    line contains `claude`. If that ever stops matching - a packaging change, a
    wrapper, a rename - the answer is not an error, it is "no sessions
    anywhere". The sweep then records every agent closed, work stops being
    delivered, and every screen agrees with itself.

    A closed mark is therefore not the disagreement; closing is how a session
    ends here. The disagreement is a snapshot still moving after we recorded
    the close. That still holds now the snapshot is pulled rather than pushed
    (D11): `collected_at` is the transcript's own last write, so only a session
    that is writing keeps it fresh, and the daemon reading a dead session's
    file does not make it look alive. A terminal Orca no longer lists is left
    to the sweep, which drops the handle and says so.
    """
    sessions = obs.live_sessions()
    if sessions is None:
        return                      # could not look; not the same as none
    handles = orca.live_terminals()
    known = set(paths.list_agents())
    con = db.connect()
    try:
        for row in db.all_agent_states(con):
            agent, handle = row["agent"], row["terminal"]
            # A row for an agent nobody has registered has no snapshot to read,
            # and a terminal that is gone is the sweep's to report.
            if agent not in known or not handle or sessions.get(agent):
                continue
            if handles is None or handle not in handles:
                continue
            own = status.read(agents.current_account(agent, con) or "",
                              paths.agent_status_file(agent))
            if own.error or own.stale or own.collected_at is None:
                continue
            closed = row["closed_at"]
            if closed and own.collected_at <= closed + WRITTEN_AFTER_CLOSE_S:
                continue
            r.add(WARN, f"agent {agent}: its transcript is still being written with no session running",
                  "only a live session writes that transcript, and the "
                  "process-table search finds no session for this agent - check "
                  "observe.is_session; while it does not match, nothing is "
                  "delivered to anyone")
    finally:
        con.close()


def _check_claude_version(r: Report) -> None:
    drv = vendors.get(agents.DEFAULT_VENDOR)
    exe = drv.binary()
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
        r.add(WARN, "could not parse the Claude Code version", textsafe.oneline(out[:80]))
        return
    version = tuple(int(x) for x in m.groups())
    shown = ".".join(str(v) for v in version)
    if version < drv.min_version:
        r.add(FAIL, f"Claude Code {shown} is too old",
              f"the usage endpoint and the transcript fields ocp reads were "
              f"measured on >= {'.'.join(str(v) for v in drv.min_version)}")
    else:
        r.add(OK, f"Claude Code {shown}")


def _check_rate_limits_present(r: Report) -> None:
    """The poll can run fine and still deliver no usage - a dead token, an
    endpoint that will not answer - which is the fail-open shape section 4
    exists to prevent. Check the field, not the file."""
    seen = False
    for acct in paths.list_accounts():
        st = status.read(acct)
        if not st.exists:
            continue
        seen = True
        src = st.pct_5h_source
        if src == "unknown" and st.rl_lost:
            r.add(WARN, f"account {acct}: the 5h reading was lost",
                  "a figure arrived with no resets_at, so we cannot place it in a window - "
                  "assignment stays refused until a session reports the window again")
        elif src == "unknown":
            r.add(WARN, f"account {acct}: no usable rate_limits in the last snapshot",
                  "the snapshot is missing, unreadable or too old - assignment stays refused")
        elif src == "carried_old":
            age = st.rl_age_s
            r.add(WARN, f"account {acct}: the 5h figure is older than we assign on",
                  f"5h {st.pct_5h}% observed {timefmt.ago(age)} ago; "
                  f"still a floor for triggers, but no new work goes here")
        elif src == "quiet":
            r.add(OK, f"account {acct}: 5h window quiet",
                  "no session has spent in the current window; reads as 0%")
        elif src == "carried":
            r.add(OK, f"account {acct}: rate_limits carried", f"5h {st.pct_5h}% (window still open)")
        else:
            r.add(OK, f"account {acct}: rate_limits present", f"5h {st.pct_5h}%")
        # The weekly window is a separate statement: wedged into the chain above
        # it stole that chain's else, so every refused account was warned about
        # and then told "rate_limits present" in the same breath.
        if st.rl7_lost:
            r.add(WARN, f"account {acct}: the 7d reading was lost",
                  "the weekly gate cannot be checked, so assignment stays refused")
    if not seen and paths.list_accounts():
        r.add(WARN, "no snapshot has rate_limits yet", "no session has run")


def _check_shared_links(r: Report) -> None:
    """A shared file an account holds as something else never reaches that
    account, and `relink_all` will not fix it."""
    bad = accounts.unlinked()
    if not bad:
        r.add(OK, "shared files reach every account")
        return
    r.add(WARN, "shared files that do not reach an account",
          ", ".join(f"{a}: {i}" for a, i in bad))


def _check_one_daemon(r: Report) -> None:
    """Exactly one supervisor, counted in the process table.

    Two on one database wake, park and notify every agent twice, and the older
    one runs whatever code it was started with. The lock names only the one
    holding it, so a duplicate that predates it shows up only here.
    """
    from ..daemon import locks as dmn_locks

    live = dmn_locks.processes()
    if len(live) > 1:
        r.add(WARN, f"{len(live)} daemons are running: pid "
                    f"{', '.join(str(x) for x in live)}",
              "end all but one - the others supervise unrecorded")
    else:
        pid = dmn_locks.running_elsewhere()
        if pid:
            r.add(OK, f"daemon running (pid {pid})")
        elif live:
            r.add(WARN, f"a daemon process (pid {live[0]}) does not hold the lock",
                  "it is not supervising - starting up, or a copy the lock refused")
        else:
            r.add(OK, "no daemon running")


def _check_service(r: Report) -> None:
    """The daemon surviving overnight is the point of `ocp service` - a
    plist on disk that launchd never loaded, or a job stuck respawning, is
    the same as no supervisor at all."""
    from ..daemon import locks as dmn_locks

    st = service.status()
    if not st["installed"]:
        # A human deliberately running it by hand in a terminal is a real,
        # working setup, not a defect - only flag the case that is actually
        # silent: nothing installed and nothing running either.
        if dmn_locks.running_elsewhere():
            r.add(OK, "no launchd service installed", "a daemon is running by hand")
        else:
            r.add(WARN, "no launchd service installed, no daemon running",
                  "`ocp service install` so it survives without a human")
        return
    if not st["loaded"]:
        r.add(FAIL, "service plist exists but is not loaded",
              f"{service.plist_path()} - `ocp service install` to reload it")
        return
    if service.crash_loop_detected():
        r.add(FAIL, "daemon is crash-looping under launchd",
              "see `ocp service logs`")
    else:
        r.add(OK, f"service loaded (pid {st['pid'] or '-'})")


def _check_autocontinue(r: Report) -> None:
    """Claude Code's own autoContinueAtUsageLimit overlaps our resume logic
    (section 4). One of them has to own it; doctor keeps the choice explicit."""
    seen = []
    for p in (vendors.shared_dir("claude") / "settings.json", paths.user_home() / ".claude" / "settings.json"):
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
    _check_mcp_config(r)
    _check_orca_hooks(r)
    _check_accounts(r)
    _check_rate_limits_present(r)
    _check_agents(r)
    _check_stuck_running(r)
    _check_launch_drift(r)
    _check_session_matches_the_record(r)
    _check_one_daemon(r)
    _check_shared_links(r)
    _check_service(r)
    _check_autocontinue(r)
    return r
