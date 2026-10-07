"""account.set_cap, account.set, account.login and account.remove: an account's settings and its login, written through the daemon, each
the founder's. They serve `hl account cap` (set_cap), the account page's Settings form with `hl account cycle` and
`recover` (set: the fields it is given, all checked before any is written) and `hl login` (login).

set_cap and set write what a retry writes again, so a retry after a lost reply is harmless. login opens a terminal, so its
handle is recorded in `write_effects` the moment it exists and a retry answers with it instead of opening a second.
`claude /login` runs in that terminal and its credentials stay with claude: the daemon reads and stores none. The terminal
belongs to no agent: `terminal.attach` watches it (see rpc_terminal)."""
from __future__ import annotations

import datetime as dt
import shlex
import shutil
import subprocess

from .. import actions, db, vendors
from ..actions import details
from ..infra import paths, textsafe
from ..session import term
from ..team import accounts
from ..usage import load as loadmod
from . import rpc, rpc_terminal

LABEL_MAX = 40


def _account(params) -> str:
    name = params.get("account")
    if not isinstance(name, str) or not name:
        raise rpc.bad_request("account is a name", "account", rpc.INVALID_PARAMS)
    if name not in paths.list_accounts():
        raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no such account: {name}", what="account", name=name)
    return name


def _cap(params) -> int | None:
    cap = params.get("cap")
    if cap is None:
        return None
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        raise rpc.bad_request("cap is a whole number, at least 1", "cap", rpc.INVALID_PARAMS)
    return cap


def _flag(params, key: str) -> bool | None:
    v = params.get(key)
    if v is not None and not isinstance(v, bool):
        raise rpc.bad_request(f"{key} is true or false", key, rpc.INVALID_PARAMS)
    return v


def _accepted(ctx, params, name: str, said: str, **more) -> dict:
    result = {"accepted": True, "said": said, **more}
    con = db.connect()
    try:
        db.record_effect(con, ctx.caller.writer, params["request_id"], name, result)
    finally:
        con.close()
    return result


def _set_cap(ctx, params) -> dict:
    acct, cap = _account(params), _cap(params)
    if cap is None:
        raise rpc.bad_request("cap is a whole number, at least 1", "cap", rpc.INVALID_PARAMS)
    ok, said = details.set_account_cap(acct, str(cap))
    if not ok:
        raise rpc.refused(said)
    return _accepted(ctx, params, "account.set_cap", said)


def _set(ctx, params) -> dict:
    acct, cap, recover, cycle_on = _account(params), _cap(params), _flag(params, "recover"), _flag(params, "cycle")
    label = params.get("label")
    if label is not None and (not isinstance(label, str) or textsafe.has_unsafe(label.strip()) or len(label.strip()) > LABEL_MAX):
        raise rpc.bad_request(f"label is one plain line of at most {LABEL_MAX} characters", "label", rpc.INVALID_PARAMS)
    end = params.get("cycle_end", ...)
    if end is not ... and end is not None:
        try:
            if not isinstance(end, str):
                raise ValueError
            dt.date.fromisoformat(end)
        except ValueError:
            raise rpc.bad_request("cycle_end is a date as YYYY-MM-DD, or null", "cycle_end", rpc.INVALID_PARAMS) from None
    said = []
    now = loadmod.label_of(acct)
    steps = [lambda: details.save_account_settings(acct, ("" if now == acct else now) if label is None else label,
                                                   "" if cap is None else str(cap), recover)]
    if end is not ...:
        steps.append(lambda: details.set_cycle_end(acct, end or ""))
    if cycle_on is not None:
        steps.append(lambda: details.set_cycle_on(acct, cycle_on))
    for step in steps:
        ok, line = step()
        if not ok:
            raise rpc.refused(line)
        said.append(line)
    return _accepted(ctx, params, "account.set", "; ".join(s for s in said if s and s != "nothing changed") or "nothing changed")


def _login(ctx, params) -> dict:
    acct = _account(params)
    said, argv, _ = actions.login_argv(acct)
    if not argv:
        raise rpc.refused(said.strip(), gate="state")
    if not term.available():
        raise rpc.refused(f"{term.missing()} - no terminal to log in in", gate="state")
    drv = vendors.for_account(acct)         # the account's config dir and nothing else of the daemon's environment goes on the command line
    command = " ".join([f"{drv.config_dir_env()}={shlex.quote(str(vendors.config_dir(acct)))}", *map(shlex.quote, argv)])
    handle = term.create_terminal(command, title=f"login-{acct}")
    if not handle:
        raise rpc.refused(f"{acct}: the terminal host could not start a terminal to log in in", gate="state")
    rpc_terminal.LOGIN_TERMINALS.add(handle)
    return _accepted(ctx, params, "account.login", f"{acct}: finish the login in the terminal", terminal=handle)


LOGOUT_TIMEOUT_S = 60


def _run_claude(argv: list[str], env: dict) -> int | None:
    """claude's logout, which the remove sequence runs before it moves the directory; None when it cannot run."""
    claude = shutil.which(argv[0])
    if not claude:
        return None
    try:
        return subprocess.run([claude, *argv[1:]], env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=LOGOUT_TIMEOUT_S).returncode
    except (OSError, subprocess.TimeoutExpired):
        return None


def _remove(ctx, params) -> dict:
    acct, dry = _account(params), _flag(params, "dry_run")
    con = db.connect()
    try:
        if dry:
            would, plan = accounts.remove_preview(acct, con)       # nothing is changed, so nothing is recorded
            return {"accepted": True, "said": plan[0], "would_remove": would, "plan": plan}
        if params.get("confirm") != acct:
            raise rpc.bad_request("confirm is the account's name, typed", "confirm", rpc.INVALID_PARAMS)
        ok, said = accounts.remove_account(acct, lambda: True, _run_claude, con)
    finally:
        con.close()
    if not ok:
        raise rpc.refused(said, gate="state")
    return _accepted(ctx, params, "account.remove", said)


rpc.METHODS["account.set_cap"] = rpc.Method(_set_cap, "founder", write=True)
rpc.METHODS["account.set"] = rpc.Method(_set, "founder", write=True)
rpc.METHODS["account.login"] = rpc.Method(_login, "founder", write=True)
rpc.METHODS["account.remove"] = rpc.Method(_remove, "founder", write=True)
