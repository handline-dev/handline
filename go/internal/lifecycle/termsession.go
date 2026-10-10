package lifecycle

import (
	"errors"
	"fmt"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/supervisor"
)

// TermSessions is a terminal agent's session as the session file it leaves reports it. It is all a handoff reads of a terminal agent:
// nothing is read from the screen and nothing is typed.
type TermSessions interface {
	// Session is the running session's pid and what it reports of itself: "idle", "busy", "waiting", or "unknown" for a process that runs
	// and whose file cannot be read. status "" means no session runs. pid is 0 when the file that names it cannot be read.
	Session(agent string) (pid int, status string)
	// PidGone is whether no process of pid runs.
	PidGone(pid int) bool
	// Tell delivers text to the running session as a message; an error is not delivered.
	Tell(agent, op, text string) error
}

// sessionAlive, sessionBusy, tell, interrupt and waitIdle are what a move asks of the agent's session, structured or in a terminal.
func (m *Manager) sessionAlive(agent string) bool {
	if m.structured(agent) {
		return m.Run.Alive(agent)
	}
	_, status := m.termSession(agent)
	return status != ""
}

// sessionBusy: a session that is not idle is busy, a permission prompt and an unreadable file included.
func (m *Manager) sessionBusy(agent string) bool {
	if m.structured(agent) {
		return m.Run.Busy(agent)
	}
	_, status := m.termSession(agent)
	return status != "" && status != "idle"
}

func (m *Manager) termSession(agent string) (int, string) {
	if m.TermRun == nil {
		return 0, ""
	}
	return m.TermRun.Session(agent)
}

func (m *Manager) tellSession(agent, op, text string) error {
	if m.structured(agent) {
		return m.Run.Send(agent, op, text)
	}
	if m.TermRun == nil {
		return errors.New("no session to tell")
	}
	return m.TermRun.Tell(agent, op, text)
}

// interruptSession ends a running turn. A terminal's turn is ended by a key, and none is pressed.
func (m *Manager) interruptSession(agent string) error {
	if m.structured(agent) {
		return m.Run.Interrupt(agent)
	}
	return errors.New("a terminal's turn cannot be interrupted without typing")
}

// waitSessionIdle waits for the running turn to end. A terminal's turn is never interrupted, so there is nothing to wait for.
func (m *Manager) waitSessionIdle(agent string, timeout time.Duration) bool {
	if m.structured(agent) {
		return m.Run.WaitIdle(agent, timeout)
	}
	return false
}

// endTerminalByPane ends a terminal agent's session by closing its pane: the relaunch opens a new terminal, so the old one must not stay.
// The end is proved by the session's pid, which the pane close alone does not show; a session still running after EndWaitS is reported
// and never relaunched over. The pane is gone by then, so the row no longer names it; the sweep records the close if the session ends later.
func (m *Manager) endTerminalByPane(agent, resume string, st SessionStatus) (bool, string) {
	fail := func(why string, pid int) (bool, string) {
		var pv any
		if pid != 0 {
			pv = pid
		}
		must(db.AddEvent(m.Q, agent, "close_failed", db.KV{K: "why", V: why}, db.KV{K: "pid", V: pv}))
		return false, why
	}
	pid, status := m.termSession(agent)
	handle := must(db.Terminal(m.Q, agent))
	switch {
	case status == "":
		return false, "no session process; nothing to close"
	case pid == 0:
		return fail("its session file cannot be read, so the end could not be proved; not closing", 0)
	case handle == "" || !m.Term.Available():
		return false, "no terminal to close"
	}
	if ok, why := m.Term.CloseTerminal(handle); !ok {
		return fail("its terminal would not close: "+why, pid)
	}
	// The handle points at nothing now, whether or not the session ends with it.
	wt := ""
	if row := must(db.GetAgentState(m.Q, agent)); row != nil {
		wt = row.Worktree.String
	}
	check(db.SetTerminal(m.Q, agent, "", wt))
	must(db.AddEvent(m.Q, agent, "terminal_closed", db.KV{K: "handle", V: handle}))
	gone := m.TermRun.PidGone(pid)
	for waited := 0; !gone && waited < supervisor.EndWaitS; waited += supervisor.EndPollS {
		m.sleep(supervisor.EndPollS * time.Second)
		gone = m.TermRun.PidGone(pid)
	}
	if !gone {
		return fail(fmt.Sprintf("its terminal was closed but the session is still running (pid %d) after %ds; not marked closed", pid, supervisor.EndWaitS), pid)
	}
	m.recordClosed(agent, resume, st)
	return true, agent + ": session ended, terminal closed"
}

// endForMove ends the session a move or a clear replaces, with no resume id: a structured one by its process group, a terminal one by its
// pane.
func (m *Manager) endForMove(agent string, st SessionStatus) (bool, string) {
	if m.structured(agent) {
		return m.endStructured(agent, "", st)
	}
	return m.endTerminalByPane(agent, "", st)
}
