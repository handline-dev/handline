package reads

import (
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
)

// attention is what this agent alone asks of the founder, the most urgent of ask, alert, stuck and running (agentview._attention): from the
// stored view's words and the agent's rows, with no live probe of the terminal.
func (e *Env) attention(name string, view map[string]any, row *db.AgentState) (any, error) {
	var asked int
	if err := e.DB.QueryRow("SELECT COUNT(*) FROM requests WHERE requester = ? AND target = ? AND state = 'queued'", name, db.Human).Scan(&asked); err != nil {
		return nil, err
	}
	if asked > 0 {
		return "ask", nil
	}
	if view["state"] == "waiting" || view["state"] == "stalled" || view["block"] != nil {
		return "alert", nil
	}
	stuck := view["wall_reason"] != nil
	if !stuck {
		var err error
		if stuck, err = e.stuckAbout(name, row); err != nil {
			return nil, err
		}
	}
	if stuck {
		return "stuck", nil
	}
	if row != nil && row.State == states.Running && !(row.ClosedAt.Valid && row.ClosedAt.Int64 != 0) {
		return "running", nil
	}
	return nil, nil
}

// stuckAbout is whether actions.stuck has a finding about this agent with Orca's terminals and the process table left unread, as the
// stored view reads it: a handoff in flight, a block, a FATAL or degraded row, a resolve nobody wrote, or a queued request that is not
// moving. This is the daemon, so the grace a self-clearing reason is given is the one actions.stuck gives while a daemon runs.
//
// Straight from actions.stuck for one agent. drops "no terminal", "terminal gone", "no session", "closed, starting" -> terminal-only, as
// in deliveryState; drops "the daemon is not running" -> this is the daemon.
func (e *Env) stuckAbout(name string, row *db.AgentState) (bool, error) {
	now := time.Now().Unix()
	if h, err := db.InFlightFor(e.DB, name); err != nil || h != nil {
		return true, err
	}
	if b, err := db.BlockGet(e.DB, name); err != nil || b != nil {
		return true, err
	}
	// An agent never started has no state row, and its queued requests are the finding below.
	if row != nil {
		if row.State == states.Stalled || row.State == states.Degraded {
			return true, nil
		}
		head, idle, found, err := e.Delivery.ForgottenResolve(e.DB, name, row, now)
		if err != nil {
			return false, err
		}
		if found && idle >= states.StuckRunningS {
			if nudged, err := db.Cursor(e.DB, "nudge:"+name); err != nil || nudged == head {
				return true, err
			}
		}
	}
	queued, err := db.QueuedAwaiting(e.DB, name)
	if err != nil || len(queued) == 0 {
		return false, err
	}
	phrase, err := e.deliveryState(name, "")
	if err != nil {
		return false, err
	}
	if phrase == "closed, nothing to wake it" || phrase == "mid-turn" {
		return false, nil
	}
	clears := phrase == "" || phrase == "closed, waking" || phrase == "mid-handoff" || phrase == "account at the wall"
	if !clears {
		return true, nil
	}
	told, err := db.Cursor(e.DB, "notify:"+name)
	if err != nil {
		return false, err
	}
	for _, r := range queued {
		if now-r.TS >= states.StuckRunningS && (phrase == "" || told < r.ID) {
			return true, nil
		}
	}
	return false, nil
}
