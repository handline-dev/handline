package agentview

import (
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/status"
	"github.com/handline/handline/go/internal/timefmt"
)

// Wall is what holds back a wake that is due: the account it would wake on is at a usage limit, or no account has a free slot.
type Wall struct {
	Text string
	// Reset is when the walled window rolls over, unix seconds; 0 for a slot or when it is not known.
	Reset int64
	Slot  bool
}

// status is the view's x-status, from the wire word and its detail and the facts they were made from.
func (e *Env) status(agent string, row *db.AgentState, state, detail string, handoff any, now int64) status.Status {
	var since int64
	if row != nil {
		since = row.Updated
	}
	at := func(st, reason, text string) status.Status {
		return status.Status{State: st, Reason: reason, Text: text, Since: since}
	}
	switch state {
	case "running":
		if row != nil && row.State == states.Handoff {
			return at(status.Running, status.Handoff, "handing off to a new session")
		}
		return at(status.Running, status.Turn, "working")
	case "starting":
		return at(status.Starting, status.Launch, "its terminal is starting")
	case "waiting":
		return at(status.Waiting, status.You, with("needs you", detail))
	case "stalled":
		return at(status.Stopped, status.Stalled, with("stalled until someone unstalls it", detail))
	}
	if row != nil && e.Walled != nil {
		if w := e.Walled(agent, row); w != nil {
			if w.Slot {
				return at(status.Stopped, status.Cap, w.Text)
			}
			s := at(status.Stopped, status.Wall, w.Text)
			s.Until = w.Reset
			return s
		}
	}
	switch state {
	case "parked":
		if detail == states.MovingMark {
			return at(status.Waiting, status.Move, "moving to another account")
		}
		s := at(status.Waiting, status.Clock, "wakes at "+timefmt.Stamp(row.WakeAt.Int64, timefmt.Clock))
		if row.WakeAt.Int64 <= now {
			s.Text = "due to wake now"
		}
		s.Until = row.WakeAt.Int64
		return s
	case "queued":
		if note := must(e.RestartRetryNote(agent, row)); note != "" {
			return at(status.Preparing, status.Retry, note)
		}
		if h, ok := handoff.(map[string]any); ok && h["phase"] == "starting" {
			return at(status.Preparing, status.Restart, "restarting: the new session begins at the next tick")
		}
		return at(status.Preparing, status.Work, "has work: starts at the next tick")
	case "no_terminal":
		return at(status.Off, status.None, "no terminal")
	}
	if row != nil && must(e.HasBackgroundWork(e.Q, agent)) {
		return at(status.Running, status.Background, detail)
	}
	if tasks := e.waitsOn(agent); len(tasks) > 0 {
		s := at(status.Waiting, status.Task, detail)
		s.Tasks = tasks
		return s
	}
	return at(status.Idle, status.None, "idle")
}

// waitsOn is the open tasks the agent's current task waits on: its child, else its blockers.
func (e *Env) waitsOn(agent string) []int64 {
	tid, ok := must2(db.CurrentTask(e.Q, agent))
	if !ok {
		return nil
	}
	if child := must(db.ChildBlockerOf(e.Q, tid)); child != nil {
		return []int64{child.ID}
	}
	var out []int64
	for _, b := range must(db.TaskBlockers(e.Q, tid)) {
		out = append(out, b.ID)
	}
	return out
}

// with is a line and what it is about, when something is.
func with(line, about string) string {
	if about == "" {
		return line
	}
	return line + ": " + about
}
