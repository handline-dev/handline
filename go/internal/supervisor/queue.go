package supervisor

import (
	"sort"

	"github.com/handline/handline/go/internal/db"
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
