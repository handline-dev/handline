package taskwrites

import (
	"database/sql"
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/textsafe"
)

// Retired is a request a release ended: held ones (the owner's landed delivery, or with RID 0 a task started bare) are
// typed a stop, queued ones are closed as rejected.
type Retired struct {
	RID               int64
	Requester, Target string
	Held, Delivered   bool
	Park              bool   // a draft took the held task back: its target is moved on to its next task
	Verdict           string // what a cancel closed it under, "" for CancelVerdict
}

// releaseMethod is task.release (actions.release_task): anyone's call, with a one-line reason, to take a doing task back to queued or
// draft. Its opener and assignee are told, and the founder too when the task was begun on their message.
func releaseMethod(home string, tell Teller) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		if p["to"] != nil {
			return nil, badParam("to is not served yet", "to")
		}
		tid, err := taskID(p)
		if err != nil {
			return nil, err
		}
		reason, ok := p["reason"].(string)
		if p["reason"] != nil && !ok {
			return nil, badParam("reason is text", "reason")
		}
		var r released
		out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			var err error
			if r, err = releaseTask(tx, home, c.Caller.Actor(), tid, reason); err != nil {
				return nil, err
			}
			return accepted(r.said(), nil), nil
		})
		if err == nil && tell != nil {
			if said := r.tell(tell, tid); said != "" {
				out["said"] = said
			}
		}
		return out, err
	}
}

// released is what a release in a transaction leaves for after its commit (tell).
type released struct {
	retired       []Retired
	notes         []told
	why           string
	outcome, tail string
}

func (r released) said() string { return fmt.Sprintf("task %s - %s%s", r.why, r.outcome, r.tail) }

// tell has the Teller type the stops, tell the requesters and move the queues up, and delivers the notices; it answers the release said
// with what the Teller did, "" when it did nothing to say.
func (r released) tell(tell Teller, tid int64) string {
	said := tell.Released(tid, r.why, r.retired)
	tell.Notified(r.notes)
	if len(said) == 0 {
		return ""
	}
	return fmt.Sprintf("task %s - %s, %s%s", r.why, strings.Join(said, "; "), r.outcome, r.tail)
}

// releaseTask takes doing task tid back to queued or draft in tx, for by with a one-line reason; a refusal is its error.
func releaseTask(tx *sql.Tx, home, by string, tid int64, reason string) (released, error) {
	var r released
	t, err := getTask(tx, tid)
	if err != nil {
		return r, err
	}
	if t == nil {
		return r, refusal(fmt.Sprintf("no task T%d", tid))
	}
	said := strings.TrimSpace(textsafe.Oneline(reason))
	r.why = fmt.Sprintf("T%d released by %s", tid, by)
	if said != "" {
		r.why += ": " + said
	}
	st, err := state(tx, t)
	if err != nil {
		return r, err
	}
	if st != "doing" {
		return r, refusal(fmt.Sprintf("task T%d is %s, not doing - nothing to release", tid, st))
	}
	reqs, err := taskRequests(tx, tid)
	if err != nil {
		return r, err
	}
	var direct []linked
	for _, q := range reqs {
		if q.asks() {
			direct = append(direct, q)
		}
	}
	begun, err := db.Begun(tx, tid)
	if err != nil {
		return r, err
	}
	// Best effort: one request losing a race to another close must not throw away what the others did.
	var ids []any
	var failed []string
	for _, q := range direct {
		if q.State == "landed" { // held by its agent: ended here, and the agent told to stop
			if err := db.EndHeld(tx, tid, q.Target); err != nil {
				return r, err
			}
			if err := db.RefreshSent(tx, tid); err != nil {
				return r, err
			}
			ids = append(ids, q.ID)
			r.retired = append(r.retired, Retired{RID: q.ID, Requester: q.Requester, Target: q.Target, Held: true})
			continue
		}
		ok, err := db.CloseIfQueued(tx, q.ID, "rejected", r.why)
		if err != nil {
			return r, err
		}
		if !ok {
			failed = append(failed, fmt.Sprintf("%s: was closed while this ran - nothing to retire", db.MsgName(tx, q.ID)))
			continue
		}
		if err := addEvent(tx, q.Requester, "closed_under", kv{"request_id", q.ID}, kv{"target", q.Target}); err != nil {
			return r, err
		}
		ids = append(ids, q.ID)
		r.retired = append(r.retired, Retired{RID: q.ID, Requester: q.Requester, Target: q.Target, Delivered: q.DeliveredAt.Valid})
	}
	if begun { // its owner holds it on the founder's word or its own: the release ends that, and the owner is told to stop
		r.retired = append(r.retired, Retired{Target: t.Owner, Held: true})
	} else if len(ids) == 0 {
		return r, refusal(fmt.Sprintf("task T%d released nothing: %s", tid, strings.Join(failed, "; ")))
	}
	payload := []kv{{"task_id", tid}, {"retired", ids}, {"reason", said}}
	if len(failed) > 0 {
		payload = append(payload, kv{"failed", failed})
		r.tail = " - not retired: " + strings.Join(failed, "; ")
	}
	if err := addEvent(tx, by, "release_task", payload...); err != nil {
		return r, err
	}
	t, err = getTask(tx, tid)
	if err != nil {
		return r, err
	}
	if _, err := tx.Exec("UPDATE tasks SET timeout_at = NULL, timeout_left = NULL WHERE id = ?", tid); err != nil {
		return r, err
	}
	back := "draft"
	if t.QueuedBy.Valid && t.QueuedBy.String != "" {
		back = "queued"
	}
	r.outcome = "back to " + back + ", its timeout paused"
	stopped := map[string]bool{} // typed a stop by the Teller, which says the same
	for _, q := range r.retired {
		if q.Held {
			stopped[q.Target] = true
		}
	}
	var to []string
	for _, who := range []string{t.OpenedBy, t.Owner, human} {
		if !stopped[who] && (who != human || begun) {
			to = append(to, who)
		}
	}
	if r.notes, err = tellTask(tx, home, by, "task "+r.why, to...); err != nil {
		return r, err
	}
	return r, nil
}
