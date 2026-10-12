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
	// PidStart is when the process of pid started, in unix seconds; 0 when it cannot be read. A pid that runs with another start time is
	// not the process that was meant: the number was reused.
	PidStart(pid int) int64
	// PaneHolds is whether pid runs inside the tmux pane handle (a descendant of the pane's process). known is false when either could not be
	// read, which is no answer.
	PaneHolds(handle string, pid int) (holds, known bool)
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
// and never relaunched over. The pane is gone by then, so the row no longer names it; SettleOrphanedClose records the close once the pid is gone.
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
	// Read before the close: once the pane is gone nothing says where the pid ran. A session outside this pane is not the one the close
	// ends; the pane's own session goes with it.
	stray := false
	if holds, known := m.TermRun.PaneHolds(handle, pid); known && !holds {
		stray = true
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
	if stray {
		why := fmt.Sprintf("a second session of this agent (pid %d) runs outside its closed terminal %s; end it by hand, or it will be taken for this agent's own", pid, handle)
		must(db.AddEvent(m.Q, agent, "needs_you", db.KV{K: "reason", V: why}, db.KV{K: "pid", V: pid}))
		m.Log("  %s: %s", agent, why)
		m.recordClosed(agent, resume, st)
		return true, agent + ": terminal closed; a second session is still running"
	}
	gone := m.TermRun.PidGone(pid)
	for waited := 0; !gone && waited < supervisor.EndWaitS; waited += supervisor.EndPollS {
		m.sleep(supervisor.EndPollS * time.Second)
		gone = m.TermRun.PidGone(pid)
	}
	if !gone {
		// The pid is kept: with the pane gone nothing else finds the session, and SettleOrphanedClose looks again every tick.
		check(db.SetCursor(m.Q, orphanKey(agent), int64(pid)))
		check(db.SetCursor(m.Q, orphanKey(agent)+":start", m.TermRun.PidStart(pid)))
		check(db.SetCursor(m.Q, orphanKey(agent)+":since", m.now()))
		return fail(fmt.Sprintf("its terminal was closed but the session is still running (pid %d) after %ds; not marked closed", pid, supervisor.EndWaitS), pid)
	}
	clearOrphan(m, agent)
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

func orphanKey(agent string) string { return "close_pending:" + agent }

// OrphanGiveUpS is how long a session that outlived its pane is waited for: past it the agent is reported as needing a person, and the
// session is still never started over.
const OrphanGiveUpS = 6 * 3600

func clearOrphan(m *Manager, agent string) {
	for _, k := range []string{orphanKey(agent), orphanKey(agent) + ":start", orphanKey(agent) + ":since", orphanKey(agent) + ":told"} {
		check(db.ClearCursor(m.Q, k))
	}
}

// orphanEnded is whether the session that outlived its pane is over: its pid is gone, or a process of that number started at another time
// (the number was reused).
func (m *Manager) orphanEnded(agent string, pid int) bool {
	if m.TermRun.PidGone(pid) {
		return true
	}
	start := must(db.Cursor(m.Q, orphanKey(agent)+":start"))
	now := m.TermRun.PidStart(pid)
	return start != 0 && now != 0 && now != start
}

// SettleOrphanedClose records the close of a terminal session that outlived its pane once its pid is gone, so the wake that follows starts
// a new one. True when it recorded the close. The pid is read from the database each tick: a restart loses nothing. A session still there
// after OrphanGiveUpS is reported once as needing a person, and left alone.
func (m *Manager) SettleOrphanedClose(agent string) bool {
	pid := int(must(db.Cursor(m.Q, orphanKey(agent))))
	if pid == 0 || m.TermRun == nil {
		return false
	}
	if row := must(db.GetAgentState(m.Q, agent)); row == nil || isClosed(row) {
		clearOrphan(m, agent)
		return false
	}
	if !m.orphanEnded(agent, pid) {
		since := must(db.Cursor(m.Q, orphanKey(agent)+":since"))
		if since != 0 && m.now()-since >= OrphanGiveUpS && must(db.Cursor(m.Q, orphanKey(agent)+":told")) == 0 {
			check(db.SetCursor(m.Q, orphanKey(agent)+":told", m.now()))
			why := fmt.Sprintf("its session (pid %d) has run %dh past its closed terminal; end it by hand, then the agent can be started", pid, (m.now()-since)/3600)
			must(db.AddEvent(m.Q, agent, "needs_you", db.KV{K: "reason", V: why}, db.KV{K: "pid", V: pid}))
			m.Log("  %s: %s", agent, why)
		}
		return false
	}
	clearOrphan(m, agent)
	m.recordClosed(agent, "", m.Status.ReadForAgent(agent, m.Agents.CurrentAccount(m.Q, agent)))
	m.afterFreshClose(agent)
	m.Log("  %s: the session that outlived its terminal has ended (pid %d)", agent, pid)
	return true
}

// afterFreshClose is what a close with no resume id leaves behind: nothing to resume, the record owed, the note spent.
func (m *Manager) afterFreshClose(agent string) {
	check(db.ForgetResume(m.Q, agent))
	check(db.SetCursor(m.Q, RecordOwedKey(agent), m.now()))
	m.Handoff.ClearNote(m.Q, agent)
}
