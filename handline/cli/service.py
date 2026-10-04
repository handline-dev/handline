"""The launchd service and the venv."""
from __future__ import annotations


from ..daemon import locks as dmn_locks
from ..infra import service
from ..infra import textsafe
from ..infra import venv as venvmod


# ---------------------------------------------------------------- service
def cmd_service_install(args) -> int:
    pid = dmn_locks.running_elsewhere()
    if pid:
        print(f"note: pid {pid} is already running by hand - the launchd job will "
              f"refuse to start (it cannot have the daemon lock) until it exits")
    ok, msg = service.install()
    print(msg)
    return 0 if ok else 1


def cmd_service_uninstall(args) -> int:
    ok, msg = service.uninstall()
    print(msg)
    return 0 if ok else 1


def cmd_service_status(args) -> int:
    st = service.status()
    print(f"label:       {service.LABEL}")
    print(f"plist:       {service.plist_path()}")
    print(f"installed:   {st['installed']}")
    print(f"loaded:      {st['loaded']}")
    print(f"pid:         {st['pid'] or '-'}")
    print(f"last exit:   {st['last_exit_code'] or '-'}")
    if service.crash_loop_detected():
        print("warning:     crash-looping - see `hl service logs`")
    return 0


def cmd_service_logs(args) -> int:
    path = service.err_log() if args.err else service.out_log()
    print(textsafe.text(service.tail(path, lines=args.lines)))
    return 0


# ---------------------------------------------------------------- venv
def cmd_venv_install(args) -> int:
    ok, msg = venvmod.install(log=print)
    print(msg)
    return 0 if ok else 1


def cmd_venv_status(args) -> int:
    ok, reason = venvmod.status()
    print(f"venv:  {venvmod.venv_dir()}")
    print(f"ready: {ok}" + (f" - {reason}" if reason else ""))
    return 0 if ok else 1
