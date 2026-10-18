package compose

import (
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/heal"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/lock"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/term"
)

// healPorts is heal.Ports over the lifecycle: the same Esc, delivery, restart and decision the founder's own buttons reach.
type healPorts struct {
	lc   *Lifecycle
	home string
}

var _ heal.Ports = healPorts{}

func (h healPorts) Agents() []string { return paths.ListAgents(h.home) }

func (h healPorts) handle(agent string) string {
	handle, _ := db.Terminal(h.lc.Manager.Q, agent)
	return handle
}

func (h healPorts) NeedsPerson(agent string) bool {
	handle := h.handle(agent)
	return handle != "" && h.lc.term.Available() && h.lc.term.ScreenBlocker(handle) != nil
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

func (h healPorts) Restart(agent string, fresh bool) (bool, string, error) {
	m := h.lc.Manager
	st := m.Status.ReadForAgent(agent, m.Agents.CurrentAccount(m.Q, agent))
	return m.RestartAgent(agent, st, lifecycle.RestartOpts{Fresh: fresh, Force: true, By: "the daemon, as it was stuck", Cause: "heal"})
}

func (h healPorts) RaiseCard(q db.Q, agent, text, fix string) error {
	var after supervisor.After
	if _, err := h.lc.Env.RaiseDecision(q, &after, agent, text, "heal", "", "", 0, 0, db.KV{K: "fix", V: fix}); err != nil {
		return err
	}
	after.Run()
	return nil
}
