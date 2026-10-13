"""The launchd service and the venv."""
from __future__ import annotations


from ..infra import daemonlock, paths, protected, service
from ..usage import load
from ..infra import textsafe
from ..infra import venv as venvmod


# ---------------------------------------------------------------- service
def cmd_service_install(args) -> int:
    pid = daemonlock.running_elsewhere()
    if pid and pid != service.status()["pid"]:
        print(f"note: pid {pid} is already running by hand - the launchd job will "
              f"refuse to start (it cannot have the daemon lock) until it exits")
    ok, msg = service.install(allow_live_home=bool(getattr(args, "allow_live_home", False)))
    print(msg)
    return 0 if ok else 1


def cmd_service_use(args) -> int:
    """Choose which daemon the service runs. Only the setting changes: the plist is rewritten by `hl service install`, so the
    switch is this and one install. Python is refused: it is being removed."""
    if args.impl == "python":
        print(service.PYTHON_RETIRED)
        return 1
    if protected.in_agent_session():
        from .hr import propose_for_session
        return propose_for_session("global", "service-use", {"value": args.impl}, args.why)
    cfg = load.config_for_write()
    cfg[service.IMPL_KEY] = args.impl
    paths.config_file().parent.mkdir(parents=True, exist_ok=True)    # a scratch home that does not exist yet
    load.write_config(cfg)
    print(f"daemon_impl is {args.impl}; run `hl service install` to apply it")
    return 0


def cmd_service_uninstall(args) -> int:
    ok, msg = service.uninstall()
    print(msg)
    return 0 if ok else 1


def cmd_service_status(args) -> int:
    st = service.status()
    print(f"label:       {service.label()}")
    print(f"plist:       {service.plist_path()}")
    print(f"installed:   {st['installed']}")
    print(f"daemon:      {service.daemon_impl()}")
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
