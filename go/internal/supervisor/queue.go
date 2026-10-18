package supervisor

import (
	"fmt"
	"sort"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/textsafe"
)

// A queued task nobody has been sent is delivered as soon as its owner is free (daemon/taskqueue.py).
//
// Delivering the next task is done at the edges (AdvanceCurrentTask after a done, a close, an unblock, a queue); an edge that finds the owner
// busy, or that never fires, leaves the task waiting for good. This reads the same picker as a level every tick, so a waiting task reaches its
// owner whatever happened to the edge.

// QueueRetryS: a delivery that landed nothing (every candidate refused) is tried again after this long. One that landed is not throttled.
const QueueRetryS = 60

// SweepTaskQueue is taskqueue.sweep.
func (e *Env) SweepTaskQueue(now int64) (err error) {
	defer Catch(&err)
	q := db.Q(e.DB)
	if _, err := e.ReleaseEndedBlockers(); err != nil {
		return err
	}
	set := map[string]bool{}
	for _, t := range must(db.OpenTasks(q)) {
		if t.QueuedBy.Valid && t.QueuedBy.String != "" && t.DoneEvidence.String == "" && t.Owner != db.Human {
			set[t.Owner] = true
		}
	}
	owners := make([]string, 0, len(set))
	for o := range set {
		owners = append(owners, o)
	}
	sort.Strings(owners)
	for _, agent := range owners {
		if must(db.CurrentTaskStillAsking(q, agent)) {
			if must(e.AdvanceToDormantBlocker(q, agent)) {
				e.Push(q, agent, 0)
				e.log("  %s: its current task waits on a task of its own behind it - delivered that one first", agent)
			} else {
				e.moveOffWaiting(q, agent)
			}
			continue
		}
		if len(must(db.WaitingTasks(q, agent))) == 0 {
			continue
		}
		key := "taskqueue:" + agent
		if now-must(db.Cursor(q, key)) < QueueRetryS {
			continue
		}
		changed, _, aerr := e.AdvanceCurrentTask(q, agent)
		if aerr != nil {
			return aerr
		}
		if changed {
			e.Push(q, agent, 0)
			e.log("  %s: a queued task was waiting and it is free - delivered", agent)
		} else {
			check(db.SetCursor(q, key, now))
		}
	}
	return nil
}

// moveOffWaiting: an idle agent whose current task is begun but waits (on a blocker, an open child, or past its timeout with no answer) is
// given its next queued task and told why. With none to give, an overdue one pings the holder once per deadline, not only its opener.
func (e *Env) moveOffWaiting(q db.Q, agent string) {
	tid, ok := ok2(db.CurrentTask(q, agent))
	cur := must(db.GetTask(q, tid))
	if !ok || cur == nil || cur.Ended() {
		return
	}
	if row := stateOf(q, agent); row == nil || (row.State != states.Idle && row.State != states.Queued) {
		return
	}
	why := must(db.BlockWait(q, agent))
	if why == "" {
		why = must(db.ChildWait(q, agent))
	}
	overdue := why == "" && must(db.Overdue(q, cur))
	if overdue {
		why = "past its timeout, unanswered"
	} else if why == "" {
		return
	}
	for _, w := range must(db.WaitingTasks(q, agent)) {
		if w.Task.ID != cur.ID && e.makeCurrent(q, agent, w, nil) {
			e.typeSay(q, agent, fmt.Sprintf("T%d is %s, so T%d (%s) is yours now: start it.", cur.ID, why, w.Task.ID, textsafe.Oneline(w.Task.Title)), "next task while current waits")
			e.Push(q, agent, 0)
			e.log("  %s: T%d is %s - delivered T%d", agent, cur.ID, why, w.Task.ID)
			return
		}
	}
	if key := fmt.Sprintf("overdue_ping:%d", cur.ID); overdue && must(db.Cursor(q, key)) != cur.TimeoutAt.Int64 {
		check(db.SetCursor(q, key, cur.TimeoutAt.Int64))
		e.typeSay(q, agent, fmt.Sprintf("T%d (%s) is %s. Keep it going: `hl task edit T%d --timeout <duration>`, or hand it back: `hl task release T%d \"<reason>\"`.",
			cur.ID, textsafe.Oneline(cur.Title), why, cur.ID, cur.ID), "overdue task, idle holder")
	}
}
