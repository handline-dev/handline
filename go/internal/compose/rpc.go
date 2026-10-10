package compose

import (
	"fmt"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/sessionrpc"
)

// SessionRPC is the env of the session methods over the lifecycle: session.start, wake, stop, restart, park and interrupt.
func (p *Parts) SessionRPC(cfg Config) *sessionrpc.Env {
	c := controls{p: p, lc: p.LC}
	return &sessionrpc.Env{DB: p.db, Home: cfg.Home, Controls: c, Procs: procs{p: p, lc: p.LC}, Now: cfg.Now}
}

// controls is sessionrpc.Controls over the lifecycle manager.
type controls struct {
	p  *Parts
	lc *Lifecycle
}

var _ sessionrpc.Controls = controls{}

func (c controls) status(agent string) lifecycle.SessionStatus {
	m := c.lc.Manager
	return m.Status.ReadForAgent(agent, m.Agents.CurrentAccount(m.Q, agent))
}

// Start is control.start_result: an agent with a worktree is started in it. Making a worktree is Orca's, which this daemon does not drive.
func (c controls) Start(_ sessionrpc.Call, agent, repo, base string) (bool, string, error) {
	m := c.lc.Manager
	if c.p.Run.Alive(agent) {
		return false, agent + ": already has a running session - wake it instead", nil
	}
	row, err := db.GetAgentState(m.Q, agent)
	if err != nil {
		return false, "", err
	}
	if m.ResumableWithoutTerminal(row) { // closed with a session to resume: resume it, whether or not anything waits
		ok, why, err := m.WakeAgent(agent, lifecycle.WakeOpts{Force: true})
		return ok, agent + ": " + why, err
	}
	if !m.Launch.ChooseAccount(m.Q, agent) {
		return false, fmt.Sprintf("%s: not started - %s", agent, m.Launch.NoRoom(agent)), nil
	}
	if repo == "" { // the agent's own repository, never the folder the daemon happens to run in
		repo = c.lc.roster.RepoOf(agent)
	}
	return c.lc.Spawns.SpawnWith(agent, repo, lifecycle.SpawnOpts{Base: base})
}

func (c controls) Wake(call sessionrpc.Call, agent string) (bool, string, error) {
	return c.lc.Manager.WakeAgent(agent, lifecycle.WakeOpts{Force: call.Person})
}

func (c controls) Close(call sessionrpc.Call, agent string, fresh bool) (bool, string, error) {
	if c.p.Run.Busy(agent) { // a person's close does not end a turn that runs: it is interrupted first
		return false, agent + " is mid-turn; interrupt it first", nil
	}
	st, o := c.status(agent), lifecycle.CloseOpts{Force: call.Person}
	if fresh {
		return c.lc.Manager.CloseFresh(agent, st, o)
	}
	return c.lc.Manager.CloseAgent(agent, st, o)
}

func (c controls) Restart(call sessionrpc.Call, agent string, fresh, force bool) (bool, string, error) {
	return c.lc.Manager.RestartAgent(agent, c.status(agent), lifecycle.RestartOpts{Fresh: fresh, Force: force, By: call.By, Cause: "restart"})
}

// Park is control.park_result: a handoff, then parked until the window rolls over. The handoff is the policy tick's (perform_handoff), which
// this daemon does not run yet.
func (c controls) Park(_ sessionrpc.Call, agent string) (bool, string, error) {
	return false, agent + ": parking is the policy tick's handoff, which this daemon does not run yet", nil
}

// procs is sessionrpc.Procs over the structured sessions. A kill holds the agent's lock as a close does, so it never runs beside a wake.
type procs struct {
	p  *Parts
	lc *Lifecycle
}

var _ sessionrpc.Procs = procs{}

func (r procs) Structured(agent string) bool { return r.p.Kind(agent) }

func (r procs) Interrupt(agent string) error {
	ok, said, err := r.lc.Manager.InterruptAgent(agent)
	if err != nil {
		return err
	}
	if !ok {
		return fmt.Errorf("%s", said)
	}
	return nil
}

func (r procs) Kill(agent string) error {
	defer r.p.Locks.Lock(agent)()
	return r.p.Manager.Kill(agent)
}

// Context is the model id and context figures of an agent on an account (agent.strip). The percent is the status file's own, nil when
// stale, as the usage read gives it; the tokens and the limit are the ones the policy tick judges the agent by. Before the lifecycle is
// built there are none.
func (p *Parts) Context(agent, account string) (string, *int, *int64, int) {
	if p.LC == nil {
		return "", nil, nil, 0
	}
	tu := tickUsage{p: p, u: p.LC.usage, roster: p.LC.roster}
	st := tu.ReadForAgent(agent, account)
	var used *int64
	if st.TrustedCtxUsed != nil {
		n := int64(*st.TrustedCtxUsed)
		used = &n
	}
	return st.ModelID, p.LC.usage.ReadForAgent(agent, account).TrustedCtxPct(), used, tu.ContextLimit(agent, st.ModelID)
}
