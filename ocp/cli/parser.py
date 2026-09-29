"""The argument parser and `main`."""
from __future__ import annotations

import argparse
import sys

from .. import actions
from ..infra import timefmt
from ..team import agents
from ..team import team

from .account import cmd_account_add, cmd_account_cap, cmd_account_list, cmd_account_recover, cmd_account_register, cmd_account_remove
from .memory import cmd_memory_migrate, cmd_memory_status
from .agent import cmd_agent_list, cmd_agent_move, cmd_agent_register, cmd_agent_show, cmd_base_promote, cmd_command, cmd_handoff_write, cmd_launch, cmd_resume, cmd_spawn, cmd_unstall
from .ask import WAIT_DEFAULT_S, cmd_ask, cmd_cancel, cmd_digest, cmd_dismiss, cmd_handoff, cmd_inbox, cmd_read, cmd_resolve, cmd_withdraw
from .bridge import cmd_bridge
from .daemon import cmd_daemon, cmd_daemon_status, cmd_dev_mode
from .health import cmd_backup, cmd_doctor, cmd_drift, cmd_events, cmd_fold, cmd_rollover, cmd_status, cmd_triggers
from .hooks import cmd_hook, cmd_hook_install, cmd_hooks_gate, cmd_hooks_status, cmd_hooks_sync
from .hr import cmd_hr, cmd_trust
from .idle import cmd_auto_accept, cmd_idle_background, cmd_idle_timeout, cmd_ignore_cache_expiry, seconds_or_never, span_arg
from .service import cmd_service_install, cmd_service_logs, cmd_service_status, cmd_service_uninstall, cmd_venv_install, cmd_venv_status
from .say import cmd_say
from .setup import cmd_guide, cmd_init, cmd_login
from .task import _expects_arg, _priority_arg, _task_arg, _timeout_arg, cmd_close, cmd_progress, cmd_task_board, cmd_task_discard, cmd_task_done, cmd_task_edit, cmd_task_link, cmd_task_list, cmd_task_merge, cmd_task_draft, cmd_task_begin, cmd_task_handoff, cmd_task_new, cmd_task_note, cmd_task_queue, cmd_task_release, cmd_task_reopen, cmd_task_start
from .team import cmd_org, cmd_team, cmd_team_check, cmd_team_init, cmd_team_seal
from .usage import cmd_collect, cmd_usage


# ---------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    from .. import __version__

    ap = argparse.ArgumentParser(prog="ocp", description="orcapipe - multi-account agent pipeline")
    ap.add_argument("--version", action="version", version=f"ocp {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create ~/.orcapipe and install shared settings").set_defaults(fn=cmd_init)

    p = sub.add_parser("login", help="log an account in (interactive), or list who is")
    p.add_argument("account", nargs="?")
    p.add_argument("--force", action="store_true",
                   help="log in again even if credentials are already there")
    p.set_defaults(fn=cmd_login)

    acc = sub.add_parser("account", help="accounts").add_subparsers(dest="sub", required=True)
    p = acc.add_parser("add", help="register an account and log it in")
    p.add_argument("name")
    p.add_argument("--no-login", action="store_true",
                   help="register only; log in later with `ocp login <name>`")
    p.add_argument("--vendor", default=None,
                   help="the tool this account is for (default: claude)")
    p.set_defaults(fn=cmd_account_add)
    p = acc.add_parser("remove", help="log an account out and take it off the roster")
    p.add_argument("name")
    p.add_argument("--yes", action="store_true", help="do not ask to confirm")
    p.set_defaults(fn=cmd_account_remove)
    p = acc.add_parser("register", help="create the account dir and link it to shared")
    p.add_argument("name")
    p.add_argument("--force", action="store_true", help="replace existing files with links")
    p.set_defaults(fn=cmd_account_register)
    p = acc.add_parser("cap", help="show or set the most agents mid-turn on an account")
    p.add_argument("name")
    p.add_argument("n", type=int, nargs="?", default=None)
    p.set_defaults(fn=cmd_account_cap)
    p = acc.add_parser("recover", help="show or set #1885's per-account usage-recovery switch")
    p.add_argument("name")
    p.add_argument("state", choices=["on", "off"], nargs="?", default=None)
    p.add_argument("--clear-relogin", action="store_true",
                   help="clear a 'needs re-login' mark left by a failed recovery")
    p.add_argument("--now", action="store_true",
                   help="run recover.recover() on this account now, by hand")
    p.set_defaults(fn=cmd_account_recover)
    p = acc.add_parser("list", help="accounts with usage")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_account_list)

    ag = sub.add_parser("agent", help="agents").add_subparsers(dest="sub", required=True)
    p = ag.add_parser("move", help="move a live session to another account now")
    p.add_argument("agent")
    p.add_argument("account")
    p.add_argument("--yes", action="store_true", help="skip the confirmation")
    p.set_defaults(fn=cmd_agent_move)
    p = ag.add_parser("register", help="register an agent; the daemon places its sessions")
    p.add_argument("name")
    p.add_argument("--vendor", default=None,
                   help=f"the tool it runs (default {agents.DEFAULT_VENDOR})")
    p.add_argument("--role", default="")
    p.add_argument("--model", default=None,
                   help="model for this agent; on re-registration, omit to keep the current one")
    p.add_argument("--effort", default=None, choices=agents.VALID_EFFORT,
                   help=f"effort level (default {agents.DEFAULT_EFFORT}); a real cost lever")
    p.add_argument("--weight", default=None, choices=["light", "heavy"])
    p.add_argument("--role-file", default=None, help="file to install as ROLE.md")
    p.add_argument("--repo", default=None,
                   help="where it works; lets the daemon start it when work arrives")
    p.add_argument("--skip-permissions", default=None, choices=["on", "off"],
                   help="start its sessions without asking before they act (default off); "
                   "applies from the next launch; on re-registration, omit to keep the current one")
    p.set_defaults(fn=cmd_agent_register)
    ag.add_parser("list", help="list agents").set_defaults(fn=cmd_agent_list)
    p = ag.add_parser("show", help="everything known about one agent")
    p.add_argument("name")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_agent_show)

    p = sub.add_parser("launch", help="exec claude as this agent's account")
    p.add_argument("agent")
    p.add_argument("--force", action="store_true", help="start even when over the usage limit")
    p.add_argument("--account", help="start on this account instead of picking one "
                   "(a move's destination)")
    p.add_argument("--prompt-file", help="a file holding the session's first prompt; "
                   "read and deleted at launch")
    p.add_argument("extra", nargs="*", help="passed through to claude")
    p.set_defaults(fn=cmd_launch)

    p = sub.add_parser("command", help="print the Orca --command string")
    p.add_argument("agent")
    p.set_defaults(fn=cmd_command)

    hk = sub.add_parser("hooks", help="mirror Orca's agent hooks").add_subparsers(dest="sub", required=True)
    p = hk.add_parser("sync", help="apply Orca's hooks to shared settings")
    p.add_argument("--watch", action="store_true", help="watch and auto-apply")
    p.add_argument("--interval", type=float, default=5.0)
    p.set_defaults(fn=cmd_hooks_sync)
    hk.add_parser("status", help="show drift").set_defaults(fn=cmd_hooks_status)
    p = hk.add_parser("gate", help="show or set one of T298's warn-only gates")
    p.add_argument("name", choices=["stop-wait", "edit-task"])
    p.add_argument("state", choices=["on", "off"], nargs="?", default=None)
    p.set_defaults(fn=cmd_hooks_gate)

    p = sub.add_parser("usage", help="the usage endpoint beside the snapshot (read-only)")
    p.add_argument("account", nargs="?", help="one account, or every account")
    p.set_defaults(fn=cmd_usage)

    p = sub.add_parser("collect", help="read each session's context from its transcript")
    p.add_argument("agent", nargs="?", help="one agent, or every agent")
    p.add_argument("--write", action="store_true",
                   help="write the snapshots, as the daemon does every tick")
    p.set_defaults(fn=cmd_collect)

    p = sub.add_parser("doctor", help="verify preconditions")
    p.add_argument("--no-color", action="store_true")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("status", help="which account may take work")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("hr", help="propose a roster change; the founder approves it")
    hrsub = p.add_subparsers(dest="hr_cmd", required=True)
    for kind in ("add", "retire", "rename"):
        q = hrsub.add_parser(kind, help=f"propose to {kind} an agent")
        q.add_argument("name")
        q.add_argument("--why", required=True, help="what the founder is deciding")
        if kind == "add":
            q.add_argument("--team", default=None, help="team in team.yaml to join")
            q.add_argument("--repo", default=None, help="where it works")
            q.add_argument("--model", default=None)
            q.add_argument("--effort", default=None, choices=sorted(agents.VALID_EFFORT))
            q.add_argument("--vendor", default=None,
                           help=f"the tool it runs (default {agents.DEFAULT_VENDOR})")
        elif kind == "rename":
            q.add_argument("--to", required=True, help="the new name")
            q.add_argument("--team", default=None, help="team it should be in after")
            q.set_defaults(repo=None, model=None, effort=None, vendor=None)
        else:
            q.set_defaults(team=None, repo=None, model=None, effort=None, vendor=None)
    for kind, what in (("link", "let one agent ask another"),
                       ("unlink", "take that permission away")):
        q = hrsub.add_parser(kind, help=f"propose to {what} (one edge in team.yaml)")
        q.add_argument("name", help="the agent that asks")
        q.add_argument("to", help="the agent asked")
        q.add_argument("--why", required=True, help="what the founder is deciding")
        if kind == "link":
            q.add_argument("--response", default=None, choices=sorted(team.VALID_RESPONSE),
                           help="async (default) waits for a reply; fire_and_forget does not")
            q.add_argument("--relation", default=None, help="e.g. peer")
        q.set_defaults(team=None, repo=None, model=None, effort=None, vendor=None)
    q = hrsub.add_parser("team", help="propose a whole team in one approval: a manager "
                                      "and its members, with the default links")
    q.add_argument("name", help="the team and its manager")
    q.add_argument("--member", action="append", required=True, metavar="SUFFIX[=REPO]",
                   help="a member, `sea` for <team>.sea, with where it works after '='; repeat")
    q.add_argument("--repo", default=None, help="where the manager works")
    q.add_argument("--model", default=None, help="for every agent in the team")
    q.add_argument("--effort", default=None, choices=sorted(agents.VALID_EFFORT))
    q.add_argument("--vendor", default=None)
    q.add_argument("--why", required=True, help="what the founder is deciding")
    q = hrsub.add_parser("list", help="proposals and what became of them")
    q.set_defaults(name=None, why=None, team=None, repo=None,
                   model=None, effort=None)
    q = hrsub.add_parser("relabel", help="name a renamed agent's Orca worktree for it "
                                         "(what a rename does; for one already applied)")
    q.add_argument("id", help="the agent's name")
    q.set_defaults(name=None, why=None, team=None, repo=None,
                   model=None, effort=None)
    q = hrsub.add_parser("rehome", help="also move the folder, once no terminal is open in it")
    q.add_argument("id", help="the agent's new name")
    q.add_argument("--from", dest="old", required=True, help="the name its folder still carries")
    q.set_defaults(name=None, why=None, team=None, repo=None,
                   model=None, effort=None)
    q = hrsub.add_parser("apply", help="carry out an approved proposal")
    q.add_argument("id")
    q.add_argument("--confirm-answer", action="store_true",
                   help="the founder only: the escalation was answered but the answer was never "
                        "recorded; show it, ask, record it and apply")
    q.add_argument("--yes", action="store_true", help="with --confirm-answer: do not ask")
    q.set_defaults(name=None, why=None, team=None, repo=None,
                   model=None, effort=None)
    q = hrsub.add_parser("withdraw", help="take back your own undecided proposal")
    q.add_argument("id")
    q.set_defaults(name=None, why=None, team=None, repo=None,
                   model=None, effort=None)
    p.set_defaults(fn=cmd_hr)

    p = sub.add_parser("trust", help="declare a folder ocp may answer claude's trust prompt in")
    trsub = p.add_subparsers(dest="trust_cmd", required=True)
    q = trsub.add_parser("add", help="propose (or, for the founder, declare) a root")
    q.add_argument("root")
    q.add_argument("--why", default=None, help="what the founder is deciding")
    q = trsub.add_parser("remove", help="drop a declared root - no gate needed")
    q.add_argument("root")
    q = trsub.add_parser("list", help="every declared root")
    q.set_defaults(root=None, why=None)
    q = trsub.add_parser("proposals", help="proposals and what became of them")
    q.set_defaults(root=None, why=None)
    q = trsub.add_parser("apply", help="carry out an approved proposal")
    q.add_argument("id")
    q.set_defaults(root=None, why=None)
    q = trsub.add_parser("migrate", help="move the old trust_roots.json into the protected "
                         "store (the founder's, once)")
    q.set_defaults(root=None, why=None)
    p.set_defaults(fn=cmd_trust)

    p = sub.add_parser("org", help="register live agents as workers in the team's Run")
    p.set_defaults(fn=cmd_org)

    p = sub.add_parser("team", help="who you may ask, and what they are doing")
    p.add_argument("--agent", default=None, help="whose team to show (default: yours)")
    p.set_defaults(fn=cmd_team)
    tm = p.add_subparsers(dest="sub")
    p = tm.add_parser("init", help="write a starter team.yaml")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_team_init)
    p = tm.add_parser("seal", help="founder only: accept team.yaml and every agent.yaml as they are now")
    p.set_defaults(fn=cmd_team_seal)
    p = tm.add_parser("check", help="validate team.yaml")
    p.add_argument("--matrix", action="store_true", help="print the full routing matrix")
    p.set_defaults(fn=cmd_team_check)

    p = sub.add_parser("ask", help="send a request to another agent")
    p.add_argument("target")
    p.add_argument("body", nargs="?", default=None)
    p.add_argument("--file", default=None, metavar="PATH",
                   help="read the body from this file instead of the inline argument - "
                        "safe against a shell expanding backticks or $() in the text")
    p.add_argument("--files", default=None, help="comma-separated paths")
    p.add_argument("--parent", type=int, default=None, help="parent request id, for chain depth")
    p.add_argument("--mergeable", action="store_true",
                   help="fold into an already-queued ask to the same target")
    p.add_argument("--first", action="store_true",
                   help="put this at the head of the target's queue")
    p.add_argument("--replaces", type=int, default=None, metavar="ID",
                   help="correct your own request ID before it has been delivered; "
                        "ID is retired and this takes its place in line")
    p.add_argument("--reason", default=None,
                   help="why --replaces retires the old request; stored as typed")
    p.add_argument("--task", type=_task_arg, default=None, metavar="T",
                   help="link this request to task T<n>; a draft task reads doing while "
                        "it is out")
    p.add_argument("--no-task", default=None, metavar="REASON",
                   help="the text names a T<n> that this request is not for; say why")
    p.add_argument("--fyi", action="store_true",
                   help="a report to the human that asks nothing of them: not red, not "
                        "counted as waiting on them, gone once read or when your next "
                        "report arrives")
    p.add_argument("--timebox", type=span_arg, default=None, metavar="DURATION",
                   help="tell you once if this is still open this long after it is "
                        "delivered (45s, 90m, 2h, 1d); default is config.yaml's "
                        "timebox_s, 2h")
    p.set_defaults(fn=cmd_ask)

    p = sub.add_parser("say", help="a message that needs no answer and starts no work (T293), typed at "
                                   "once - work is `ocp task new`")
    p.add_argument("target", nargs="?")
    p.add_argument("body", nargs="?", default=None)
    p.add_argument("--file", default=None, metavar="PATH",
                   help="read the body from this file instead of the inline argument")
    p.add_argument("--parent", type=int, default=None,
                   help="the say this replies to, for its thread - a reply to a say is a say")
    p.add_argument("--task", type=_task_arg, default=None, metavar="T",
                   help="a say to your report about this task: it lands as a note on T<n>")
    p.add_argument("--fyi", default=None, metavar="WHY",
                   help="a say to your report that is not work: say why (the reason is a check, not stored)")
    p.set_defaults(fn=cmd_say)

    p = sub.add_parser("withdraw", help="take back your own request before it is delivered")
    p.add_argument("id", type=int)
    p.add_argument("--reason", default=None, help="why; stored as typed")
    p.set_defaults(fn=cmd_withdraw)

    p = sub.add_parser("cancel", help="the founder takes a request out of an agent's queue")
    p.add_argument("id", type=int)
    p.add_argument("--yes", action="store_true", help="do not ask to confirm")
    p.set_defaults(fn=cmd_cancel)

    p = sub.add_parser("inbox", help="the one request currently waiting for an agent")
    p.add_argument("agent", nargs="?")
    p.add_argument("--all", action="store_true", help="the whole queue and its history")
    p.add_argument("--wait", action="store_true", help="block until something new arrives, then print it")
    p.add_argument("--timeout", type=int, default=WAIT_DEFAULT_S, metavar="SECONDS",
                   help=f"how long --wait waits before giving up (default {WAIT_DEFAULT_S}s)")
    p.set_defaults(fn=cmd_inbox)

    p = sub.add_parser("read", help="print one request whole, with its answer")
    p.add_argument("id", type=int)
    p.add_argument("--wait", action="store_true", help="block until it has been answered, then print it")
    p.add_argument("--timeout", type=int, default=WAIT_DEFAULT_S, metavar="SECONDS",
                   help=f"how long --wait waits before giving up (default {WAIT_DEFAULT_S}s)")
    p.set_defaults(fn=cmd_read)

    p = sub.add_parser("resolve", help="close a request with a verdict")
    p.add_argument("id", type=int)
    p.add_argument("verdict", nargs="?", default=None)
    p.add_argument("--file", default=None, metavar="PATH",
                   help="read the verdict from this file instead of the inline argument - "
                        "safe against a shell expanding backticks or $() in the text")
    p.add_argument("--state", default="done", choices=["done", "rejected"])
    p.add_argument("--task", type=_task_arg, nargs="+", metavar="T",
                   help="the task(s) this answer belongs to; required on the founder's requests")
    p.add_argument("--no-task", metavar="REASON",
                   help="answered, nothing to build; required on the founder's requests "
                        "unless --task is given")
    p.add_argument("--finding", action="append", metavar="TITLE",
                   help="a non-blocking finding you are deferring rather than blocking "
                        "on; opens as its own task, owned by whoever you are answering, "
                        "linked to this request - repeat for more than one")
    p.add_argument("--retires", type=int, default=None, metavar="ID",
                   help="an older request this answer makes moot")
    p.add_argument("--hold", metavar="TITLE",
                   help="this answer puts the work on hold: it opens as a task owned by "
                        "you, under the task this request belongs to, so it stays on the "
                        "board instead of reading done")
    p.add_argument("--on", type=_task_arg, nargs="+", metavar="T",
                   help="with --hold: the open task(s) it waits on; you are told when the "
                        "last one closes")
    p.set_defaults(fn=cmd_resolve)

    p = sub.add_parser("dismiss", help="take one row off your mailbox without replying")
    p.add_argument("id", type=int)
    p.add_argument("--reason", default=None, help="why; stored as typed")
    p.add_argument("--task", type=_task_arg, nargs="+", metavar="T",
                   help="the task(s) this belongs to; required to dismiss a request the founder made")
    p.add_argument("--no-task", metavar="REASON",
                   help="nothing to build; required to dismiss a founder request unless --task is given")
    p.set_defaults(fn=cmd_dismiss)

    p = sub.add_parser("fold", help="fold events into the digest (the one model call)")
    p.add_argument("agent", nargs="?")
    p.add_argument("--no-model", action="store_true", help="deterministic fold only")
    p.add_argument("--model", default=None, help="default: the driver's own cheap model")
    p.add_argument("--account", default=None, help="account to run the fold call on")
    p.add_argument("--repo", default=None, help="repo to read git facts from")
    p.set_defaults(fn=cmd_fold)

    p = sub.add_parser("rollover", help="midnight rollover (no model)")
    p.add_argument("agent", nargs="?")
    p.set_defaults(fn=cmd_rollover)

    p = sub.add_parser("backup", help="snapshot OCP_HOME if it changed; `install` runs it every 5 minutes")
    p.add_argument("action", nargs="?", choices=["install"])
    p.set_defaults(fn=cmd_backup)

    p = sub.add_parser("digest", help="print an agent's digest")
    p.add_argument("agent")
    p.add_argument("--date", default=None)
    p.set_defaults(fn=cmd_digest)

    p = sub.add_parser("handoff", help="print the handoff waiting for an agent")
    p.add_argument("agent")
    p.set_defaults(fn=cmd_handoff)

    p = sub.add_parser("events", help="list recorded events")
    p.add_argument("--agent", default=None)
    p.add_argument("--after", type=int, default=0)
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--all", action="store_true",
                   help="include the hook events that only mark turn edges")
    p.set_defaults(fn=cmd_events)

    p = sub.add_parser("hook", help="internal: record a hook event from stdin")
    p.add_argument("event")
    p.set_defaults(fn=cmd_hook)

    p = sub.add_parser("install-hooks", help="add ocp event hooks to shared settings")
    p.set_defaults(fn=cmd_hook_install)

    p = sub.add_parser("daemon", help="run the supervisor loop")
    p.add_argument("--once", action="store_true", help="one tick, then exit")
    p.add_argument("--max-ticks", type=int, default=None)
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--foreground", action="store_true",
                   help="accepted for symmetry; the daemon always runs in the foreground")
    p.set_defaults(fn=cmd_daemon)

    p = sub.add_parser("ps", help="daemon and agent state")
    p.set_defaults(fn=cmd_daemon_status)

    p = sub.add_parser("dev-mode",
                       help="daemon restarts itself when its own code changes - off by default")
    p.add_argument("state", nargs="?", choices=["on", "off"], help="omit to print the current state")
    p.set_defaults(fn=cmd_dev_mode)

    sv = sub.add_parser("service", help="run the daemon as a launchd LaunchAgent").add_subparsers(
        dest="sub", required=True)
    p = sv.add_parser("install", help="write the plist and load it into launchd")
    p.set_defaults(fn=cmd_service_install)
    p = sv.add_parser("uninstall", help="unload the job and remove the plist")
    p.set_defaults(fn=cmd_service_uninstall)
    p = sv.add_parser("status", help="installed / loaded / pid / crash-looping")
    p.set_defaults(fn=cmd_service_status)
    p = sv.add_parser("logs", help="tail the daemon's launchd-captured output")
    p.add_argument("-n", "--lines", type=int, default=60)
    p.add_argument("--err", action="store_true", help="show stderr instead of stdout")
    p.set_defaults(fn=cmd_service_logs)

    vsub = sub.add_parser("venv", help="the interpreter ocp itself runs under").add_subparsers(
        dest="sub", required=True)
    p = vsub.add_parser("install", help="create or refresh $OCP_HOME/venv from requirements.txt")
    p.set_defaults(fn=cmd_venv_install)
    p = vsub.add_parser("status", help="whether the venv exists and matches requirements.txt")
    p.set_defaults(fn=cmd_venv_status)

    p = sub.add_parser("triggers", help="what the daemon would decide right now")
    p.set_defaults(fn=cmd_triggers)

    p = sub.add_parser("idle-timeout", help="show or set how long an idle agent may sit "
                       "before the daemon closes it (T52)")
    p.add_argument("seconds", type=seconds_or_never, nargs="?", default=None,
                   help="seconds, or 'never'; omit to show the current value")
    p.add_argument("--agent", default=None,
                   help="set only this agent's own override, instead of the global default")
    p.add_argument("--clear", action="store_true",
                   help="with --agent, drop its override and follow the global setting again")
    p.set_defaults(fn=cmd_idle_timeout)

    p = sub.add_parser("idle-background", help="show or set when an idle agent still running background "
                       "work is told to finish or stop it, and how long after that it is stopped (T424)")
    p.add_argument("--notice", type=span_arg, default=None, metavar="SPAN",
                   help="how long it may show nothing before the notice (90s, 10m); default is its idle timeout")
    p.add_argument("--grace", type=span_arg, default=None, metavar="SPAN",
                   help="how long after the notice its work is stopped if it still shows nothing; default 5m")
    p.add_argument("--agent", default=None,
                   help="set only this agent's own override, instead of the global default")
    p.add_argument("--clear", action="store_true",
                   help="with --agent, drop both its overrides and follow the global settings again")
    p.set_defaults(fn=cmd_idle_background)

    p = sub.add_parser("ignore-cache-expiry", help="show or set whether a resume may reuse "
                       "a session past its prompt cache's expiry instead of starting fresh (T52)")
    p.add_argument("state", choices=["on", "off"], nargs="?", default=None,
                   help="omit to show the current value")
    p.add_argument("--agent", default=None,
                   help="set only this agent's own override, instead of the global default")
    p.add_argument("--clear", action="store_true",
                   help="with --agent, drop its override and follow the global setting again")
    p.set_defaults(fn=cmd_ignore_cache_expiry)

    p = sub.add_parser("auto-accept", help="show or set whether ocp answers the dialogs a working "
                       "agent meets all day: a browser tool's prompt, a declared folder's trust screen (T64)")
    p.add_argument("state", choices=["on", "off"], nargs="?", default=None,
                   help="omit to show the current value")
    p.set_defaults(fn=cmd_auto_accept)

    p = sub.add_parser("unstall", help="clear STALLED (humans only)")
    p.add_argument("agent")
    p.set_defaults(fn=cmd_unstall)

    p = sub.add_parser("drift", help="has the base moved under this agent?")
    p.add_argument("repo")
    p.add_argument("--base", default="main")
    p.add_argument("--no-fetch", action="store_true")
    p.set_defaults(fn=cmd_drift)

    p = sub.add_parser("spawn", help="give an agent its own worktree and start it there")
    p.add_argument("agent")
    p.add_argument("--repo", required=True)
    p.add_argument("--base", default="main")
    p.add_argument("--prompt", default=None, help="first task to send once it is up")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_spawn)

    p = sub.add_parser("bridge", aliases=["top", "watch"],
                       help="the screen the team is run from")
    p.add_argument("--once", action="store_true", help="print the screen as text and exit")
    p.add_argument("--textual", action="store_true",
                   help="accepted and does nothing: Textual is the only screen")
    p.set_defaults(fn=cmd_bridge)

    bs = sub.add_parser("base", help="the per-session fixed context").add_subparsers(
        dest="sub", required=True)
    p = bs.add_parser("promote", help="add a fact to an agent's base.md")
    p.add_argument("agent")
    p.add_argument("--item", required=True)
    p.add_argument("--force", action="store_true", help="accept going over budget")
    p.set_defaults(fn=cmd_base_promote)

    ms = sub.add_parser("memory", help="per-agent memory against its budget").add_subparsers(
        dest="sub", required=True)
    p = ms.add_parser("status", help="bytes and entries against budget, files not indexed "
                      "or unused for 28 days")
    p.add_argument("agent", nargs="?")
    p.set_defaults(fn=cmd_memory_status)
    p = ms.add_parser("migrate", help="move shared/memory topic files to their owners "
                      "(dry run unless --apply)")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(fn=cmd_memory_migrate)

    p = sub.add_parser("progress", help="keep a note of what is done and what is left on "
                       "your request in progress (no note shows it; '' clears it)")
    p.add_argument("note", nargs="?", default=None)
    p.set_defaults(fn=cmd_progress)

    p = sub.add_parser("write-handoff", help="record a handoff now")
    p.add_argument("agent")
    p.set_defaults(fn=cmd_handoff_write)

    p = sub.add_parser("close", help="close a done task (ocp close T7), the asker's or "
                                     "the founder's sign-off, or end an idle agent's session, keeping "
                                     "its terminal")
    p.add_argument("target", help="a task (T<n>), or an agent's name")
    p.add_argument("evidence", nargs="?",
                   help="refused - `ocp task done` takes the evidence now")
    p.set_defaults(fn=cmd_close)

    tk = sub.add_parser("task", help="the task board").add_subparsers(dest="sub", required=True)
    p = tk.add_parser("new", help="open and deliver a task in one step (T292): a request "
                                  "to --owner, checked against the routing table")
    p.add_argument("title")
    p.add_argument("body", nargs="?", default=None,
                   help="the work itself, sent to --owner; required - the title is not a brief")
    p.add_argument("--owner", required=True, help="an agent, or 'human' for a decision task")
    p.add_argument("--parent", type=_task_arg, default=None, metavar="T",
                   help="the task this is a part of, written T<number>")
    p.add_argument("--blocked-by", type=_task_arg, default=None, metavar="T",
                   help="cannot be worked until this task closes")
    p.add_argument("--priority", type=_priority_arg, default=None, metavar="P",
                   help="P1 (high), P2 (default) or P3 (low)")
    p.add_argument("--expects", type=_expects_arg, default="report", metavar="K",
                   help="what `task done` must give back: sha, verdict, decision or "
                        "report (default)")
    p.add_argument("-t", "--timeout", type=_timeout_arg, default=None, metavar="DURATION",
                   help="required: notify the opener each time this long passes with the task "
                   f"still open (90m, 2h; at most {timefmt.span(actions.MAX_TASK_TIMEOUT_S)})")
    p.set_defaults(fn=cmd_task_new)
    p = tk.add_parser("begin", help="start a task the founder told you to do, without approval "
                                    "(T297): opens it for you, linked to their message")
    p.add_argument("title")
    p.add_argument("--chat", type=int, required=True, metavar="ID",
                   help="the founder's message to you (a request id): what you were told")
    p.add_argument("--why", required=True, help="what led to this, in a line")
    p.add_argument("--priority", type=_priority_arg, default=None, metavar="P",
                   help="P1 (high), P2 (default) or P3 (low)")
    p.add_argument("--expects", type=_expects_arg, default="report", metavar="K",
                   help="what `task done` must give back: sha, verdict, decision or report (default)")
    p.set_defaults(fn=cmd_task_begin)
    p = tk.add_parser("handoff", help="hand T<n> to another agent with a note (T377): owner -> "
                                      "auditor -> back to the opener on a PASS or to whoever handed it "
                                      "over on a FAIL; the opener passes it to a merger")
    p.add_argument("task", type=_task_arg, metavar="T")
    p.add_argument("to", help="the agent that takes it")
    p.add_argument("note", nargs="?", default=None,
                   help="required: what changed, and what the receiver should know "
                        "(PASS/FAIL, test results, how to reproduce a FAIL)")
    p.add_argument("--file", default=None, metavar="PATH",
                   help="read the note from this file instead of the inline argument - "
                        "safe against a shell expanding backticks or $() in the text")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--pass", dest="verdict", action="store_const", const="PASS",
                   help="an auditor's verdict: on to the opener, who may then pass it to a merger")
    g.add_argument("--fail", dest="verdict", action="store_const", const="FAIL",
                   help="an auditor's verdict: back to whoever handed it over")
    p.set_defaults(fn=cmd_task_handoff)
    p = tk.add_parser("start", help="pick up T<n> as your current task (T292) - for one "
                                    "delivered while you already held another")
    p.add_argument("task", type=_task_arg, metavar="T")
    p.set_defaults(fn=cmd_task_start)
    p = tk.add_parser("note", help="a mid-task correction to whoever holds T<n> (T292) - "
                                   "the opener or the founder only; new work is a child task")
    p.add_argument("task", type=_task_arg, metavar="T")
    p.add_argument("text")
    p.set_defaults(fn=cmd_task_note)
    p = tk.add_parser("list", help="the open tasks and their requests")
    p.add_argument("--all", action="store_true", help="closed tasks too, with what closed them")
    p.add_argument("--owner", default=None, help="only this agent's tasks")
    p.add_argument("--everyone", action="store_true",
                   help="every team's tasks, not just yours and your reports'")
    p.add_argument("--state", default=None,
                   choices=("draft", "queued", "doing", "done", "closed", "discarded", "merged"),
                   help="only tasks in this state; a closed one shows even without --all")
    p.set_defaults(fn=cmd_task_list)
    p = tk.add_parser("queue", help="approve draft tasks to run - the founder or the owner's manager")
    p.add_argument("tasks", type=_task_arg, nargs="+", metavar="T")
    p.set_defaults(fn=cmd_task_queue)
    p = tk.add_parser("draft", help="send queued tasks back to draft - the founder or the owner's manager")
    p.add_argument("tasks", type=_task_arg, nargs="+", metavar="T")
    p.set_defaults(fn=cmd_task_draft)
    p = tk.add_parser("link", help="attach a further request to a task")
    p.add_argument("task", type=_task_arg, metavar="T")
    p.add_argument("request", type=int)
    p.set_defaults(fn=cmd_task_link)
    p = tk.add_parser("edit", help="change a task's owner and/or title - opener or "
                                   "founder only, and never silent about it")
    p.add_argument("task", type=_task_arg, metavar="T")
    p.add_argument("--owner", default=None, help="fixing who was named, not handing off work")
    p.add_argument("--title", default=None)
    p.add_argument("--priority", type=_priority_arg, default=None, metavar="P",
                   help="P1 (high), P2 (default) or P3 (low)")
    p.add_argument("--blocked-by", type=_task_arg, default=None, metavar="T",
                   help="cannot be worked until this task closes")
    p.add_argument("--unblock", type=_task_arg, default=None, metavar="T",
                   help="remove that blocking task")
    p.add_argument("--timeout", type=_timeout_arg, default=None, metavar="DURATION",
                   help="extend the timeout, counting from now (90m, 2h; at most "
                   f"{timefmt.span(actions.MAX_TASK_TIMEOUT_S)})")
    p.set_defaults(fn=cmd_task_edit)
    p = tk.add_parser("merge", help="fold tasks into one: the kept task takes their titles, "
                                    "requests and sub-tasks, the rest close as merged - "
                                    "opener or founder only")
    p.add_argument("keep", type=_task_arg, metavar="T", help="the task that stays")
    p.add_argument("tasks", type=_task_arg, nargs="+", metavar="T", help="the tasks folded in")
    p.set_defaults(fn=cmd_task_merge)
    p = tk.add_parser("done", help="mark a task done (#71) - owner or a linked request's "
                                   "target only, exactly one of a sha, --request N, or a reason")
    p.add_argument("task", type=_task_arg, metavar="T")
    p.add_argument("evidence", nargs="?", help="a merge sha on main, or a reason")
    p.add_argument("--request", type=int, default=None,
                   help="a request where the result was already reported")
    p.add_argument("--repo", default=None, help="the repo whose main a sha is checked against")
    p.add_argument("--reason", action="store_true",
                   help="record the evidence as words even if it looks like hex")
    p.set_defaults(fn=cmd_task_done)
    p = tk.add_parser("reopen", help="send a done task back to doing (#71) - the asker "
                                     "or the founder; needs a one-line reason")
    p.add_argument("task", type=_task_arg, metavar="T")
    p.add_argument("reason")
    p.set_defaults(fn=cmd_task_reopen)
    p = tk.add_parser("release", help="T286: take a doing task back to queued/draft - the "
                                      "opener or the founder, the other answer to a timeout "
                                      "notice besides `edit --timeout`")
    p.add_argument("task", type=_task_arg, metavar="T")
    p.set_defaults(fn=cmd_task_release)
    p = tk.add_parser("discard", help="throw away a task that is not done (#85) - the asker or the founder; "
                                      "needs a one-line reason")
    p.add_argument("task", type=_task_arg, metavar="T")
    p.add_argument("reason")
    p.set_defaults(fn=cmd_task_discard)
    p = tk.add_parser("board", help="your open tasks and your reports', and who holds each now")
    p.add_argument("--everyone", action="store_true",
                   help="every team's tasks, not just yours and your reports'")
    p.set_defaults(fn=cmd_task_board)

    p = sub.add_parser("resume", help="hand a parked agent back its handoff")
    p.add_argument("agent")
    p.set_defaults(fn=cmd_resume)

    p = sub.add_parser("guide", help="load detail on demand")
    p.add_argument("topic", nargs="?")
    p.set_defaults(fn=cmd_guide)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except SystemExit:
        raise
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
