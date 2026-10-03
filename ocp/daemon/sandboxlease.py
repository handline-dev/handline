"""The founder's leave to run an agent without its sandbox lasts as long as one session.

`agents.sandbox_lease` is `granted` from the moment the founder turns the sandbox off, and `active` once
a session launched with it off is up, stamped with that launch. When that session is over - the agent is
recorded closed, or its recorded launch is another one, as when a wake comes before this sweep - the
leave is ended: the key leaves agent.yaml and the lease its protected store, so the next session starts
sandboxed. A leave given while the agent is closed waits (`granted`) for the session that takes it.
"""
from __future__ import annotations

from .. import db
from ..infra import paths, protected
from ..session import launch
from ..team import agents


def sweep(con, log) -> None:
    for agent in paths.list_agents():
        try:
            lease = agents.sandbox_lease(agent)
            if lease is None:
                continue
            row = db.get_agent_state(con, agent)
            closed = bool(row and row["closed_at"])
            if lease == agents.SANDBOX_PENDING and row and not closed and launch.launched_sandbox_off(row):
                protected.put(agents._SANDBOX_NS, agent, f"{agents.SANDBOX_ACTIVE}:{launch.launched_at(row)}")
                log(f"  {agent}: running without its sandbox, until that session ends")
            elif lease == agents.SANDBOX_ACTIVE and (
                    closed or not row or launch.launched_at(row) != agents.sandbox_lease_launch(agent)):
                agents.register(agent, sandbox=True)
                log(f"  {agent}: its session ended; the sandbox is back on")
        except Exception as e:  # noqa: BLE001 - one agent's record must not stop the rest
            log(f"  {agent}: sandbox lease: {type(e).__name__}: {e}")
