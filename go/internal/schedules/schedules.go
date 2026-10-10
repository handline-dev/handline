// Package schedules is daemon/schedules.py's runner: messages the daemon types at an agent on a cron schedule, set per agent.
//
// An agent must not schedule its own wakeups (a loop dies with its session), so the daemon does it. When a schedule comes due the message
// is typed like a say. A schedule that fell due while the agent was RUNNING waits for it to stop; several missed times are one message, and
// one still undelivered is never followed by another. A level read from a cursor per schedule: the minute it was last handled, after which
// any time of the expression up to now is due.
package schedules

import (
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/states"
)

// Requester is who a scheduled message is from.
const Requester = "schedule"

// Runner sweeps the schedules of Agents.
type Runner struct {
	Q         db.Q
	Agents    func() []string
	Schedules func(agent string) []reads.Schedule // reads.Env.SchedulesOf: none for an agent whose value cannot be read
	// Say writes the message to the agent's inbox, calls recorded with its id, and tells the agent (supervisor.Env.SystemSayThen).
	Say func(requester, target, body string, recorded func(rid int64)) error
	Log func(string)
}

// Sweep is schedules.sweep: one agent's schedule failing is not the tick's.
func (r *Runner) Sweep(now int64) error {
	for _, agent := range r.Agents() {
		if err := r.agent(agent, r.Schedules(agent), now); err != nil {
			r.Log(fmt.Sprintf("  %s: schedule failed: %v", agent, err))
		}
	}
	return nil
}

// ready is whether there is somewhere to type it now: a session that is not mid-turn, or none (started or woken to read it).
func ready(row *db.AgentState) bool {
	if row == nil {
		return true
	}
	switch row.State {
	case states.Running, states.Handoff, states.Stalled:
		return false
	}
	return true
}

// forgetRemoved drops the cursors of schedules the agent no longer has, so one added back later is new.
func (r *Runner) forgetRemoved(agent string, keep map[string]bool) error {
	rows, err := r.Q.Query("SELECT name FROM cursors WHERE name LIKE ? OR name LIKE ?", "schedule_at:"+agent+":%", "schedule_rid:"+agent+":%")
	if err != nil {
		return err
	}
	var names []string
	for rows.Next() {
		var n string
		if err := rows.Scan(&n); err != nil {
			rows.Close()
			return err
		}
		names = append(names, n)
	}
	rows.Close()
	for _, n := range names {
		if !keep[n[strings.LastIndex(n, ":")+1:]] {
			if err := db.ClearCursor(r.Q, n); err != nil {
				return err
			}
		}
	}
	return nil
}

func (r *Runner) agent(agent string, items []reads.Schedule, now int64) error {
	keep := map[string]bool{}
	for _, it := range items {
		keep[reads.ScheduleSID(it)] = true
	}
	if err := r.forgetRemoved(agent, keep); err != nil {
		return err
	}
	minute := now - now%60
	for _, it := range items {
		key := reads.ScheduleSID(it)
		at, ridKey := "schedule_at:"+agent+":"+key, "schedule_rid:"+agent+":"+key
		handled, err := db.Cursor(r.Q, at)
		if err != nil {
			return err
		}
		if handled == 0 { // seen for the first time: it counts from now, not from the past
			if err := db.SetCursor(r.Q, at, minute); err != nil {
				return err
			}
			continue
		}
		due, ok := reads.NextCronAfter(it.Cron, handled)
		if !ok || due > now {
			continue
		}
		last, err := db.Cursor(r.Q, ridKey)
		if err != nil {
			return err
		}
		if last != 0 {
			pending, err := db.GetRequest(r.Q, last)
			if err != nil {
				return err
			}
			if pending != nil && pending.State == "queued" && !pending.DeliveredAt.Valid {
				// the last one is still unread: these times are that one
				if err := db.SetCursor(r.Q, at, minute); err != nil {
					return err
				}
				continue
			}
		}
		row, err := db.GetAgentState(r.Q, agent)
		if err != nil {
			return err
		}
		if !ready(row) {
			continue // due, and stays due until the agent stops
		}
		body := fmt.Sprintf("[schedule %s] %s", it.Cron, it.Message)
		var recErr error
		err = r.Say(Requester, agent, body, func(rid int64) {
			if recErr = db.SetCursor(r.Q, at, minute); recErr != nil {
				return
			}
			if recErr = db.SetCursor(r.Q, ridKey, rid); recErr != nil {
				return
			}
			_, recErr = db.AddEvent(r.Q, agent, "schedule_fired", db.KV{K: "cron", V: it.Cron}, db.KV{K: "request_id", V: rid})
			r.Log(fmt.Sprintf("  %s: schedule '%s' is due - message #%d sent", agent, it.Cron, rid))
		})
		if err == nil {
			err = recErr
		}
		if err != nil {
			return err
		}
	}
	return nil
}
