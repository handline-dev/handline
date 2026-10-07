"""The daemon's control socket (T351): a local Unix socket, mode 0600, the
only way in to type at an agent's terminal or touch its session. A connection
carries JSON-RPC calls (`rpc.py`); a legacy `{verb}` request still gets its one
`{ok, said}` reply and a hangup, as it always did - no polling, no second
message on that connection.

Every verb here runs on a connection of its own (`db.connect()` per request,
the same rule `control.perform` already follows) and, where it touches an
agent's terminal, under that agent's `lock.terminal` - the same lock the
tick holds, so a request arriving mid-tick loses the race cleanly instead of
landing beside what the tick just typed. A connection handled in its own
thread: `wake` alone can hold the socket open for `WAKE_SESSION_TIMEOUT_S`
waiting for a session to appear, and a slow verb for one agent must not hold
up a `message` to another.

A refused connection (no socket file, or nothing listening) is the caller's
only sign the daemon is down (#2073 decision A) - `client.send` returns None
for it, and prints nothing here: what to do about it is the caller's.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time

from .. import db
from ..actions import control, details, prompt_blocks
from ..infra import paths, sessiontoken
from ..team import agent_settings

from . import landauth, lands, messaging, relay, rpc, sends, stream
from . import (  # noqa: F401 - importing a module registers its methods; one per line, sorted, so branches adding modules do not meet
    rpc_accounts,
    rpc_agents,
    rpc_answers,
    rpc_chat,
    rpc_chat_now,
    rpc_chatwrites,
    rpc_hooks,
    rpc_inbox,
    rpc_inbox_get,
    rpc_input,
    rpc_lists,
    rpc_operations,
    rpc_options,
    rpc_outlook,
    rpc_prompts,
    rpc_schedules,
    rpc_session,
    rpc_settings,
    rpc_status,
    rpc_stream,
    rpc_tasks,
    rpc_taskverbs,
    rpc_taskview,
    rpc_hr,
    rpc_team,
    rpc_terminal,
    rpc_typing,
    rpc_worktrees,
)



def _wake(con, log, **kw) -> tuple[bool, str]:
    return True, control.wake(con, kw["agent"])


def _close(con, log, **kw) -> tuple[bool, str]:
    return control.close_result(con, kw["agent"], bool(kw.get("fresh")))


def _restart(con, log, **kw) -> tuple[bool, str]:
    return control.restart_result(con, kw["agent"], bool(kw.get("fresh")), bool(kw.get("force")),
                                  str(kw.get("by") or db.HUMAN))


def _park(con, log, **kw) -> tuple[bool, str]:
    return True, control.park(con, kw["agent"])


def _fold(con, log, **kw) -> tuple[bool, str]:
    return True, control.fold(con, kw["agent"])


def _move(con, log, **kw) -> tuple[bool, str]:
    return True, control.move(con, kw["agent"], kw["account"])


def _start(con, log, **kw) -> tuple[bool, str]:
    return True, control.start(con, kw["agent"], kw.get("repo"), kw.get("base", "main"))


def _message(con, log, **kw) -> tuple[bool, str]:
    return messaging.send_message(con, kw["agent"], kw["text"], log)


def _answer_block(con, log, **kw) -> tuple[bool, str]:
    return prompt_blocks.answer(con, int(kw["block"]), dict(kw.get("choice") or {}))


def _trust(con, log, **kw) -> tuple[bool, str]:
    worktrees = [(a, w) for a, w in kw["worktrees"]]
    return details.trust_worktrees(kw["account"], worktrees)


def _agent_set(con, log, **kw) -> tuple[bool, str]:
    return agent_settings.set_by_agent(kw["agent"], dict(kw.get("values") or {}), kw.get("speaker"))


def _land(con, log, **kw) -> tuple[bool, str]:
    return lands.request(con, kw["sha"], kw["message"], kw.get("tree"), kw.get("caller"))


def _land_pass(con, log, **kw) -> tuple[bool, str]:
    return lands.record_pass(con, kw["sha"], kw.get("caller"))


VERBS = {
    "land": _land,
    "land_pass": _land_pass,
    "agent_set": _agent_set,
    "message": _message,
    "say": sends.say_verb,
    "push": sends.push_verb,
    "push_answer": sends.push_answer_verb,
    "notify_cancelled": sends.notify_cancelled_verb,
    "wake": _wake,
    "close": _close,
    "restart": _restart,
    "park": _park,
    "fold": _fold,
    "move": _move,
    "start": _start,
    "answer_block": _answer_block,
    "trust": _trust,
}


# Verbs that act on the strength of who the caller is: the name in the request counts only when the
# process that sent it descends from the session `hl launch` recorded for that agent (infra/sessiontoken.py).
TOKEN_VERBS = ("land", "land_pass")


def _land_authorize(payload: dict, log) -> dict:
    """The session's PreToolUse hook vouching for the `hl land` command it is about to run
    (session/landgate.py). Only the hook can name the per-session key."""
    agent, verb = payload.get("agent"), payload.get("land_verb")
    if verb not in TOKEN_VERBS or not sessiontoken.key_ok(agent, payload.get("key")):
        log(f"  land_authorize refused for {str(agent)[:60]!r}")
        return {"ok": False, "said": "not authorized"}
    landauth.grant(agent, verb, str(payload.get("sha")))
    log(f"  land_authorize: {str(agent)[:60]!r} vouched for {verb} {str(payload.get('sha'))[:40]!r}")
    return {"ok": True, "said": "vouched"}


def _handle_request(payload: dict, log, peer: int | None = None) -> dict:
    verb = payload.get("verb")
    if verb == "land_authorize":
        return _land_authorize(payload, log)
    fn = VERBS.get(verb)
    if fn is None:
        return {"ok": False, "said": f"not a socket verb: {verb!r}"}
    kw = {k: v for k, v in payload.items() if k not in ("verb", "caller", "token")}
    if verb in TOKEN_VERBS:
        agent = payload.get("agent")
        if not sessiontoken.verify(agent, peer):
            log(f"  {verb} refused: caller is not the session of {str(agent)[:60]!r}")
            return {"ok": False, "said": f"{agent or 'the caller'} is not this request's session: it must come from a "
                    f"session `hl launch` recorded for that agent (relaunch an older one)"}
        if not landauth.take(agent, verb, str(payload.get("sha"))):
            log(f"  {verb} refused: nothing the session's own command vouched for ({str(agent)[:60]!r})")
            return {"ok": False, "said": f"{verb} is accepted only from the session's own `hl land` command, "
                    f"not from other code it runs (a test run, a script)"}
        kw["caller"] = agent
    con = None
    try:
        con = db.connect()
        ok, said = fn(con, log, **kw)
        return {"ok": bool(ok), "said": str(said)}
    except db.SchemaError as e:     # an empty reply would read to the caller as "the daemon is not running"
        log(f"  {verb}: {e}")
        return {"ok": False, "said": rpc.schema_said(e)}
    except Exception as e:  # noqa: BLE001 - a bad request must reply, not kill the thread
        return {"ok": False, "said": f"{verb}: {e}"}
    finally:
        if con is not None:
            con.close()


def _serve_one(conn: socket.socket, log) -> None:
    try:
        rpc.Connection(conn, log, _handle_request).serve()
    except OSError:
        pass        # the client went away or stalled past the timeout
    finally:
        conn.close()


MAX_CONNECTIONS = 256       # served at once; one past it is answered `daemon_busy` and closed


class SocketServer:
    """Runs the accept loop in a background thread, one further thread per
    connection - see the module docstring for why neither is inline with the
    tick."""

    def __init__(self, log) -> None:
        self._log = log
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._watcher = stream.Watcher(log)
        self._open, self._open_lock = 0, threading.Lock()
        self._relay = relay.RelayServer(log)

    def start(self) -> None:
        path = paths.socket_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        # The daemon singleton lock already guarantees we are the only
        # daemon for this OCP_HOME, so a file left at this path is a stale
        # one from a process that did not exit cleanly, not a live listener.
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        # Created private via umask, not just chmod after: chmod alone leaves
        # a window where the file is briefly world-writable.
        old_umask = os.umask(0o177)
        try:
            sock.bind(str(path))
        finally:
            os.umask(old_umask)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass  # the umask above already narrowed it; this is only extra insurance
        sock.listen(64)
        sock.settimeout(1.0)  # so `stop()` is noticed instead of blocking accept() forever
        self._sock = sock
        self._thread = threading.Thread(target=self._accept_loop, daemon=True,
                                        name="ocp-daemon-socket")
        self._thread.start()
        try:
            self._relay.start()
        except OSError as e:
            self._log(f"relay socket: could not start ({e}) - continuing without it")
        self._watcher.start()

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break  # the socket was closed under us by stop()
            with self._open_lock:
                full = self._open >= MAX_CONNECTIONS
                self._open += not full
            if full:
                self._refuse_busy(conn)
                continue
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        try:
            _serve_one(conn, self._log)
        finally:
            with self._open_lock:
                self._open -= 1

    def _refuse_busy(self, conn: socket.socket) -> None:
        """One line saying why, then the connection is closed: the daemon serves as many connections as it can and says so past that."""
        try:
            conn.settimeout(1.0)
            conn.sendall(json.dumps({"jsonrpc": "2.0", "id": None, "error": rpc.RpcError(rpc.BUSY, "daemon_busy", f"{MAX_CONNECTIONS} connections are open: try again").wire()}).encode() + b"\n")
        except OSError:
            pass
        finally:
            conn.close()

    def stop(self) -> None:
        self._stop.set()
        self._relay.stop()
        self._watcher.close()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)
        try:
            os.unlink(paths.socket_file())
        except FileNotFoundError:
            pass
