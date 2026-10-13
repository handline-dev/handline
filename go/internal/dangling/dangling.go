// Package dangling closes terminals the daemon opened for an agent that are no longer the agent's current one. A second session left
// running in such a pane is outside the daemon's sight, and one of them once acted as the founder.
//
// A recorded terminal (agent_terminals, with an agent, not closed) is dangling while its pane stands and the agent's row names another
// terminal or none. A dangling pane with no session in it is closed at once. A pane with a session is first asked for that session's
// handoff, to a file of its own, and closed once the file is written or AskWaitS has passed. A session that outlives its pane is ended by
// its pid, and only while the pid's start time is the one read at the ask: a reused number is never hit. Each close is logged, kept as an
// event and told to the agent's manager.
//
// A terminal opened with no agent, or never recorded (the founder's own tmux), is never touched; nor is a pane whose process started after
// its row was written, which is another pane under a reused handle.
package dangling

import (
	"fmt"
	"os"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/team"
)

const (
	// AskWaitS is how long a session in a dangling pane has to write its handoff before the pane is closed.
	AskWaitS = 300
	// OpenSlackS is how much later than its row a recorded pane's process may have started: a spawn opens the pane, then records it.
	OpenSlackS = 60
	// Requester is who the manager's notice comes from.
	Requester = "daemon"
)

// Ports is what the sweep reads and does beyond the database.
type Ports interface {
	// Live is every terminal the host has, nil when it could not say.
	Live() map[string]bool
	// PaneStarted is when the pane's own process started, in unix seconds; 0 when it cannot be read.
	PaneStarted(handle string) int64
	// PaneSession is the pid of the agent session running inside the pane, 0 for none; known is false when it could not be read.
	PaneSession(handle string) (pid int, known bool)
	// Ask delivers text to the session of pid; an error is not delivered.
	Ask(pid int, text string) error
	Close(handle string) (bool, string)
	PidGone(pid int) bool
	// PidStart is when the process of pid started, in unix seconds; 0 when it cannot be read.
	PidStart(pid int) int64
	Kill(pid int) error
	Tell(to, text string) error
}

// Sweep is the tick's pass over the recorded terminals.
type Sweep struct {
	Q     db.Q
	Home  string
	Ports Ports
	Log   func(format string, a ...any)
}

const askText = "This session runs in %s's terminal %s, which is not its current one, so the terminal will be closed in %d minutes. " +
	"Write your handoff to %s now with one Write: what you were doing, what is left, and who you wait on. Then stop and run nothing else."

func askKey(handle string) string   { return "dangling:" + handle }
func pidKey(handle string) string   { return "dangling:" + handle + ":pid" }
func startKey(handle string) string { return "dangling:" + handle + ":start" }

func (s *Sweep) log(format string, a ...any) {
	if s.Log != nil {
		s.Log(format, a...)
	}
}

// Run looks at every open recorded terminal once.
func (s *Sweep) Run(now int64) error {
	live := s.Ports.Live()
	if live == nil {
		return nil
	}
	rows, err := db.OpenAgentTerminals(s.Q)
	if err != nil {
		return err
	}
	for _, r := range rows {
		if err := s.one(r, live[r.Handle], now); err != nil {
			return err
		}
	}
	return nil
}

func (s *Sweep) one(r db.OpenTerminal, standing bool, now int64) error {
	pid, err := db.Cursor(s.Q, pidKey(r.Handle))
	if err != nil {
		return err
	}
	if !standing {
		if pid != 0 && !s.ended(r.Handle, int(pid)) {
			return s.end(r, int(pid))
		}
		return s.settle(r.Handle)
	}
	current, err := db.Terminal(s.Q, r.Agent)
	if err != nil || current == r.Handle {
		return err
	}
	started := s.Ports.PaneStarted(r.Handle)
	if started == 0 {
		return nil
	}
	if started > r.OpenedAt+OpenSlackS {
		s.log("  %s: terminal %s is another pane under a reused handle; its record is closed, the pane left alone", r.Agent, r.Handle)
		return s.settle(r.Handle)
	}
	spid, known := s.Ports.PaneSession(r.Handle)
	if !known {
		return nil
	}
	note := paths.StrayHandoffFile(s.Home, r.Agent, spid)
	if spid != 0 {
		asked, err := db.Cursor(s.Q, askKey(r.Handle))
		if err != nil {
			return err
		}
		if asked == 0 || int(pid) != spid {
			return s.ask(r, spid, note, now)
		}
		if _, err := os.Stat(note); err != nil && now-asked < AskWaitS {
			return nil
		}
	}
	ok, why := s.Ports.Close(r.Handle)
	if !ok {
		s.log("  %s: its dangling terminal %s would not close: %s", r.Agent, r.Handle, why)
		return nil
	}
	if _, err := db.AddEvent(s.Q, r.Agent, "terminal_closed", db.KV{K: "handle", V: r.Handle}, db.KV{K: "why", V: "dangling"}); err != nil {
		return err
	}
	s.notify(r, current, spid, note)
	if spid == 0 || s.ended(r.Handle, spid) {
		return s.settle(r.Handle)
	}
	return nil // the session outlived its pane: the next look ends it by its pid
}

func (s *Sweep) ask(r db.OpenTerminal, pid int, note string, now int64) error {
	if err := os.Remove(note); err != nil && !os.IsNotExist(err) {
		return err
	}
	if err := s.Ports.Ask(pid, fmt.Sprintf(askText, r.Agent, r.Handle, AskWaitS/60, note)); err != nil {
		s.log("  %s: could not ask the session (pid %d) in its dangling terminal %s for its handoff: %v", r.Agent, pid, r.Handle, err)
	} else {
		s.log("  %s: asked the session (pid %d) in its dangling terminal %s for its handoff", r.Agent, pid, r.Handle)
	}
	for k, v := range map[string]int64{askKey(r.Handle): now, pidKey(r.Handle): int64(pid), startKey(r.Handle): s.Ports.PidStart(pid)} {
		if err := db.SetCursor(s.Q, k, v); err != nil {
			return err
		}
	}
	return nil
}

// ended is whether the session of pid is over: no process has that number, or the one that has it started at another time.
func (s *Sweep) ended(handle string, pid int) bool {
	if s.Ports.PidGone(pid) {
		return true
	}
	start, _ := db.Cursor(s.Q, startKey(handle))
	now := s.Ports.PidStart(pid)
	return start != 0 && now != 0 && now != start
}

// end stops a session that outlived its closed pane. With no start time read at the ask, the pid cannot be proved to be that session: it
// is left to a person, once.
func (s *Sweep) end(r db.OpenTerminal, pid int) error {
	start, err := db.Cursor(s.Q, startKey(r.Handle))
	if err != nil {
		return err
	}
	if start == 0 {
		s.tell(r.Agent, fmt.Sprintf("%s's session (pid %d) outlived its closed terminal %s and could not be proved to be that session; end it by hand", r.Agent, pid, r.Handle))
		return s.settle(r.Handle)
	}
	if err := s.Ports.Kill(pid); err != nil {
		s.log("  %s: could not end the session (pid %d) that outlived its terminal %s: %v", r.Agent, pid, r.Handle, err)
		return nil
	}
	s.log("  %s: ended the session (pid %d) that outlived its terminal %s", r.Agent, pid, r.Handle)
	return nil
}

func (s *Sweep) notify(r db.OpenTerminal, current string, pid int, note string) {
	what := "no session ran in it"
	if pid != 0 {
		what = fmt.Sprintf("its session (pid %d) was asked for a handoff", pid)
		if _, err := os.Stat(note); err == nil {
			what += ", written to " + note
		} else {
			what += " and wrote none"
		}
	}
	if current == "" {
		current = "none"
	}
	s.tell(r.Agent, fmt.Sprintf("closed %s's terminal %s: not its current terminal (%s); %s", r.Agent, r.Handle, current, what))
}

func (s *Sweep) tell(agent, text string) {
	s.log("  %s: %s", agent, text)
	to := team.ParentName(agent)
	if to == "" {
		to = db.Human
	}
	if err := s.Ports.Tell(to, text); err != nil {
		s.log("  %s: telling %s: %v", agent, to, err)
	}
}

// settle records the terminal closed and forgets what was kept of it.
func (s *Sweep) settle(handle string) error {
	for _, k := range []string{askKey(handle), pidKey(handle), startKey(handle)} {
		if err := db.ClearCursor(s.Q, k); err != nil {
			return err
		}
	}
	return db.CloseAgentTerminal(s.Q, handle)
}
