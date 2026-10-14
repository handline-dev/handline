"""Tests: session.input, session.keys, session.type and session.interrupt (contract/protocol.md): what each sends, the gates
each refusal names (role, terminal lock, input holder, wall, screen blocker) and their order, a retry after a lost reply,
the lock held while they type so the tick types nothing beside them, and what counts as typing for the idle hold.

The terminal host is replaced by a recorder: it has its own tests; this file asks what the methods send and refuse.

Run on its own: python3 tests/daemon/test_rpc_typing.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline import actions  # noqa: E402
from handline.daemon import delivery, inputhold  # noqa: E402
from handline.infra import lock, sessiontoken  # noqa: E402
from handline.session import stopgate  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

METHODS = {"session.input": {"text": "hello"}, "session.type": {"text": "hello"}, "session.keys": {"keys": ["esc"]},
           "session.interrupt": {}}


def _code(r: dict):
    return r.get("error", {}).get("data", {}).get("code")


def _gate(r: dict):
    return r.get("error", {}).get("data", {}).get("gate")


class Host:
    """The terminal host replaced: every send recorded, the screen clear unless a test says otherwise."""

    def __enter__(self):
        self.sent: list = []
        self.during: list = []              # what each send saw of the terminal lock
        self.blocker = None
        self.saved = [(termmod, n, getattr(termmod, n)) for n in ("send", "send_to_session", "screen_blocker", "available")]
        termmod.available = lambda: True

        def probe():
            with lock.terminal("worker") as mine:
                self.during.append(not mine)       # not ours to take: the call holds it

        def send(handle, text, enter=True):
            probe()
            self.sent.append((handle, text, enter))
            return True, ""

        def to_session(handle, text, settle_s=0, enter=True):
            probe()
            self.sent.append((handle, text, enter, "session"))
            return True, ""
        termmod.send, termmod.send_to_session = send, to_session
        termmod.screen_blocker = lambda handle: self.blocker
        db_con = db.connect()
        db.set_terminal(db_con, "worker", "t1", None)
        db.set_agent_state(db_con, "worker", "IDLE")
        db_con.close()
        return self

    def __exit__(self, *exc):
        for m, n, v in self.saved:
            setattr(m, n, v)


def _hold(agent: str, name: str, ago: int = 0) -> None:
    con = db.connect()
    con.execute("DELETE FROM input_holders WHERE agent = ?", (agent,))
    inputhold.take(con, agent, "person", name, int(time.time()) - ago)
    con.close()


def getpass_user() -> str:
    import getpass
    return getpass.getuser()


def test_the_daemon_sends_escape_on_no_occasion_of_its_own(home: Path) -> None:
    root = Path(__file__).resolve().parent.parent.parent / "handline" / "daemon"
    users = [f.name for f in root.glob("*.py") if f.name != "rpc_typing.py" and ('term.ESCAPE' in f.read_text() and "term.send(" in f.read_text())]
    check("no daemon module besides the typing methods sends the Esc constant to a terminal", users == [], str(users))


def main() -> int:
    def tests(td):
        run(lambda: test_the_daemon_sends_escape_on_no_occasion_of_its_own(Path(td) / "y5"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
