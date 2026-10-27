package compose

import (
	"github.com/handline/handline/go/internal/agentview"
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/heal"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/lock"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/term"
)

// healPorts is heal.Ports over the lifecycle: the same Esc, delivery, restart and decision the founder's own buttons reach.
type healPorts struct {
	lc    *Lifecycle
	home  string
	needs func(agent string) *agentview.NeedsYou
}

var _ heal.Ports = healPorts{}

func (h healPorts) Agents() []string { return paths.ListAgents(h.home) }

func (h healPorts) handle(agent string) string {
	handle, _ := db.Terminal(h.lc.Manager.Q, agent)
	return handle
}

// NeedsPerson: with the hooks on, an open needs-you card answers first; then the screen (term.DialogUp).
func (h healPorts) NeedsPerson(agent string) bool {
	if h.lc.term.Turns != nil && h.needs != nil && h.needs(agent) != nil {
		return true
	}
	handle := h.handle(agent)
	return handle != "" && h.lc.term.Available() && h.lc.term.DialogUp(handle) != nil
}

func (h healPorts) Interrupt(agent string) (bool, string) {
	hold := lock.TakeTerminal(h.home, agent)
	if hold == nil {
		return false, lifecycle.TypingElsewhere
	}
	defer hold.Release()
	handle := h.handle(agent)
	if handle == "" || !h.lc.term.Available() {
		return false, "no terminal"
	}
	return h.lc.term.Send(handle, term.Escape, false)
}

func (h healPorts) Push(q db.Q, agent string, rid int64) string { return h.lc.Env.Push(q, agent, rid) }

func (h healPorts) LoadHeld(agent string) string { return h.lc.Gate.Held(agent) }

// Restart takes a RUNNING agent out of its turn without waiting for it (RestartHung); one between turns is restarted as a person's
// restart is.
func (h healPorts) Restart(agent string, fresh bool) (bool, string, error) {
	m := h.lc.Manager
	st := m.Status.ReadForAgent(agent, m.Agents.CurrentAccount(m.Q, agent))
	const by, cause = "the daemon, as it was stuck", "heal"
	if row, err := db.GetAgentState(m.Q, agent); err != nil {
		return false, "", err
	} else if row != nil && row.State == states.Running {
		return m.RestartHung(agent, st, fresh, by, cause)
	}
	return m.RestartAgent(agent, st, lifecycle.RestartOpts{Fresh: fresh, By: by, Cause: cause})
}
