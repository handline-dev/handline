package supervisor

import (
	"crypto/sha1"
	"encoding/binary"
	"os"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/structured"
)

// Seeing and typing at an agent's session (daemon/delivery.py, "sending").

const (
	// SeenFreshS is how old the sweep's reading may be before a send looks again. A tick is 2s, so anything older came from another tick.
	SeenFreshS = 10
	// DrawnFreshS is how long one screen read's answer holds.
	DrawnFreshS = 5
	// ExitDialogWaitS is how long after typing /exit to look for the dialog it may raise.
	ExitDialogWaitS = 6
	EndPollS        = 1
	// DialogStall begins the detail of an agent stalled on a dialog ocp cannot get past.
	DialogStall = "cannot get past a dialog"
	// MaxTypedLines is the most lines of text typed at an agent: Claude Code wraps a longer paste in <pasted_content>.
	MaxTypedLines = 3
)

// TableListsSession is what the process table says: yes if it lists a session for the agent, no if not; known false when it could not be read.
func (e *Env) TableListsSession(agent string) (yes, known bool) {
	e.init()
	e.mu.Lock()
	o, have := e.seen[agent]
	fresh := have && e.nowT().Sub(e.seenAt) <= SeenFreshS*time.Second
	e.mu.Unlock()
	if fresh {
		if !o.SessionSeen {
			return false, false
		}
		return o.HasPID, true
	}
	sessions, ok := e.Observer.LiveSessions()
	if !ok {
		return false, false
	}
	return sessions[agent], true
}

// ScreenDrawsSession is whether the agent's terminal draws a session right now: the screen's word, held DrawnFreshS.
func (e *Env) screenDrawsSession(agent string) bool {
	e.init()
	e.mu.Lock()
	hit, have := e.drawn[agent]
	e.mu.Unlock()
	if have && e.nowT().Sub(hit.at) <= DrawnFreshS*time.Second {
		return hit.yes
	}
	handle := must(db.Terminal(e.DB, agent))
	answer := handle != "" && e.Terminal.Available() && e.SessionStillDrawn(handle, false)
	e.mu.Lock()
	e.drawn[agent] = drawnAt{e.nowT(), answer}
	e.mu.Unlock()
	return answer
}

// SessionPresent is whether there is a session to type into, or only the shell it left behind. Fail-closed, with two witnesses: a
// process the table lists, or a session drawn on the agent's terminal. An unreadable table with nothing drawn is a no.
func (e *Env) sessionPresent(agent string) bool {
	if yes, _ := e.TableListsSession(agent); yes {
		return true
	}
	return e.screenDrawsSession(agent)
}

// WaitSessionGone is whether the agent's session is out of a fresh reading of the process table, within EndWaitS. An unreadable table is a no.
func (e *Env) WaitSessionGone(agent string) bool {
	deadline := e.nowT().Add(EndWaitS * time.Second)
	for {
		sessions, ok := e.Observer.LiveSessions()
		if ok && !sessions[agent] {
			e.mu.Lock()
			delete(e.seen, agent)
			e.mu.Unlock()
			return true
		}
		if !e.nowT().Before(deadline) {
			return false
		}
		e.sleep(EndPollS * time.Second)
	}
}

// SessionStillDrawn is whether an agent's session is still drawn on this terminal's screen; with wait the screen is given EndWaitS to
// clear. A screen that cannot be read is not a yes.
func (e *Env) SessionStillDrawn(handle string, wait bool) bool {
	if handle == "" {
		return false
	}
	deadline := e.nowT()
	if wait {
		deadline = deadline.Add(EndWaitS * time.Second)
	}
	for {
		if session, known := e.Terminal.SessionOnScreen(handle); !session || !known {
			return false
		}
		if !e.nowT().Before(deadline) {
			return true
		}
		e.sleep(EndPollS * time.Second)
	}
}

// WaitForSession is whether the launch typed started a session, within timeoutS. With a prompt file, the session counts only once that
// file is gone.
func (e *Env) WaitForSession(agent string, timeoutS int, prompt string) bool {
	deadline := e.nowT().Add(time.Duration(timeoutS) * time.Second)
	for {
		sessions, ok := e.Observer.LiveSessions()
		if ok && sessions[agent] && !(prompt != "" && fileExists(prompt)) {
			return true
		}
		if !e.nowT().Before(deadline) {
			return false
		}
		e.sleep(time.Second)
	}
}

func fileExists(p string) bool { _, err := os.Stat(p); return err == nil }

// pastDialog gets past a dialog before typing an exit or a clear: "" when nothing stands in the way, else why. While a reason stands,
// when the dialog first proved unpassable is kept by its title.
func (e *Env) pastDialog(q db.Q, agent string, onlyLimit bool) string {
	why, title := e.Dialogs.PastDialog(q, agent, onlyLimit)
	sinceKey, titleKey := "dialog_since:"+agent, "dialog_title:"+agent
	if why == "" {
		check(db.ClearCursor(q, sinceKey))
		check(db.ClearCursor(q, titleKey))
		return ""
	}
	// A dialog whose title did not parse is never the same as the last: its clock restarts, which errs toward not stalling.
	var mark int64
	if title != "" {
		sum := sha1.Sum([]byte(title))
		mark = int64(binary.BigEndian.Uint32(sum[:4]))
	}
	if mark == 0 || must(db.Cursor(q, titleKey)) != mark || must(db.Cursor(q, sinceKey)) == 0 {
		check(db.SetCursor(q, titleKey, mark))
		check(db.SetCursor(q, sinceKey, e.now()))
	}
	return why
}

// StalledOnDialog: a dialog ocp cannot get past, standing as long as a block takes to be raised with the founder, stops the agent.
func (e *Env) stalledOnDialog(q db.Q, agent, why string) bool {
	since := must(db.Cursor(q, "dialog_since:"+agent))
	if since == 0 || e.now()-since < e.Dialogs.DecisionAfterS() {
		return false
	}
	check(db.SetAgentState(q, agent, states.Stalled, 0, DialogStall+": "+why))
	e.log("  %s: STALLED - %s: %s", agent, DialogStall, why)
	return true
}

// ClearOwed is when the agent was told its clear is coming, 0 when none is owed.
func (e *Env) clearOwed(q db.Q, agent string) int64 { return must(db.Cursor(q, "clear_owed:"+agent)) }

// WallReason is why typing at this agent's open session would only spend quota, "" for none. Checked where the keystrokes are sent.
func (e *Env) wallReason(q db.Q, agent string, row *db.AgentState) string {
	if e.Waits.AtLimit(q, agent) {
		return agent + " is at its usage limit"
	}
	if row == nil {
		row = stateOf(q, agent)
	}
	if row != nil && row.State == states.Parked && !row.ClosedAt.Valid {
		if kind := must(db.LastHandoffTrigger(q, agent)); kind != "" && e.Sessions.MovableTrigger(kind) {
			return agent + " is parked until its usage window rolls over"
		}
	}
	return ""
}

func (e *Env) liveSession(q db.Q, agent string) string {
	if e.Sessions == nil {
		return ""
	}
	return e.Sessions.LiveSession(q, agent)
}

// SessionChanged is whether the session on screen is a different one from the one the head was typed into. An id that cannot be read
// is not a difference.
func (e *Env) sessionChanged(q db.Q, agent string) bool {
	live := e.liveSession(q, agent)
	return live != "" && live != must(db.NotifiedSession(q, agent))
}

// TypeNotice tells one line to an agent's open session. False when it has none: nothing is typed at a terminal.
func (e *Env) TypeNotice(q db.Q, agent, text string) (sent bool, err error) {
	defer Catch(&err)
	if !e.structuredAgent(agent) {
		return false, nil
	}
	state, _ := e.sendOnce(q, agent, e.nextOp(q, "notice"), text, structured.Now)
	return state != NotSent, nil
}

// NoteFailure remembers why the agent's wake or start failed this tick; "" forgets it.
func (e *Env) NoteFailure(agent, why string) {
	e.init()
	e.mu.Lock()
	defer e.mu.Unlock()
	if why != "" {
		e.failed[agent] = failure{why, e.now()}
	} else {
		delete(e.failed, agent)
	}
}

// NoteHold remembers why work could not reach the agent this tick; "" forgets it. contestable is for a reason a screen read gives and the
// agent's own state may contradict.
func (e *Env) NoteHold(agent, why string, contestable bool) {
	e.init()
	e.mu.Lock()
	defer e.mu.Unlock()
	if why != "" {
		e.noted[agent] = noted{why, e.now(), contestable}
	} else {
		delete(e.noted, agent)
	}
}

// HeldFreshS is how long a recorded reason stays true without being renewed.
const HeldFreshS = 30

// FailedReason is why the agent's latest wake or start failed, "" when that is not recent.
func (e *Env) FailedReason(agent string, now int64) string {
	e.init()
	e.mu.Lock()
	defer e.mu.Unlock()
	if f, ok := e.failed[agent]; ok && now-f.at <= HeldFreshS {
		return f.why
	}
	return ""
}

// HeldReason is (why, contestable) the agent's work was not typed on its latest try; ok false when that is not recent.
func (e *Env) HeldReason(agent string, now int64) (why string, contestable, ok bool) {
	e.init()
	e.mu.Lock()
	defer e.mu.Unlock()
	if n, have := e.noted[agent]; have && now-n.at <= HeldFreshS {
		return n.why, n.contestable, true
	}
	return "", false, false
}

func cut(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		return string(r[:n])
	}
	return s
}
