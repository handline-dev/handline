package lifecycle

import (
	"sync"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
)

// AgentLocks serialises lifecycle acts on one agent: a tick and a person asking for the same move, or a spawn, must not act twice. The zero value is ready; a nil *AgentLocks locks nothing.
type AgentLocks struct {
	mu    sync.Mutex
	locks map[string]*sync.Mutex
}

// Lock takes agent's lock; the returned func releases it.
func (a *AgentLocks) Lock(agent string) func() {
	if a == nil {
		return func() {}
	}
	a.mu.Lock()
	if a.locks == nil {
		a.locks = map[string]*sync.Mutex{}
	}
	l := a.locks[agent]
	if l == nil {
		l = &sync.Mutex{}
		a.locks[agent] = l
	}
	a.mu.Unlock()
	l.Lock()
	return l.Unlock
}

// hasSession: the agent has a terminal to talk to.
func (m *Manager) hasSession(agent string, row *db.AgentState) bool {
	return row != nil && row.Terminal.String != ""
}

func (m *Manager) lockAgent(agent string) func() {
	m.once.Do(func() {
		if m.Locks == nil {
			m.Locks = &AgentLocks{}
		}
	})
	return m.Locks.Lock(agent)
}

// recordClosed is what a session that has ended leaves: the closed mark and the way back.
func (m *Manager) recordClosed(agent, resume string, st SessionStatus) {
	check(db.SetClosed(m.Q, agent, resume))
	if row := must(db.GetAgentState(m.Q, agent)); row != nil && row.State == states.Running {
		check(db.SetAgentState(m.Q, agent, states.Idle, row.WakeAt.Int64, "session closed while idle"))
	}
	var sv any
	if st.SessionID != "" {
		sv = st.SessionID
	}
	must(db.AddEvent(m.Q, agent, "close", db.KV{K: "session", V: sv}, db.KV{K: "ctx_pct", V: st.CtxPct}))
	m.Log("  %s: session closed (resume %s)", agent, orUnknown(st.SessionID))
}

// waitIdle waits for the turn a forced close or restart must not cut off.
func (m *Manager) waitIdle(agent, handle string) bool {
	return handle != "" && m.Term.WaitIdle(handle)
}
