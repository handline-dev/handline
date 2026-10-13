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
	Notify            bool // the requester is told of the close
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
		by := c.Caller.Actor()
		var (
			retired       []Retired
			notes         []told
			why           string
			outcome, tail string
		)
		out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			retired, notes, outcome, tail = nil, nil, "", ""
			t, err := getTask(tx, tid)
			if err != nil {
				return nil, err
			}
			if t == nil {
				return nil, refusal(fmt.Sprintf("no task T%d", tid))
			}
			said := strings.TrimSpace(textsafe.Oneline(reason))
			if said == "" {
				return nil, refusal("releasing needs a one-line reason")
			}
			why = fmt.Sprintf("T%d released by %s: %s", tid, by, said)
			st, err := state(tx, t)
			if err != nil {
				return nil, err
			}
			if st != "doing" {
				return nil, refusal(fmt.Sprintf("task T%d is %s, not doing - nothing to release", tid, st))
			}
			reqs, err := taskRequests(tx, tid)
			if err != nil {
				return nil, err
			}
			var direct []linked
			for _, r := range reqs {
				if r.asks() {
					direct = append(direct, r)
				}
			}
			bare, err := startedBare(tx, tid)
			if err != nil {
				return nil, err
			}
			begun, err := db.Begun(tx, tid)
			if err != nil {
				return nil, err
			}
			if len(direct) == 0 && !bare {
				if child, err := childAsking(tx, tid); err != nil {
					return nil, err
				} else if child != 0 {
					return nil, refusal(fmt.Sprintf("task T%d is doing through its child T%d - release it there", tid, child))
				}
			}
			// Best effort: one request losing a race to another close must not throw away what the others did.
			var ids []any
			var failed []string
			for _, r := range direct {
				if r.State == "landed" { // held by its agent: ended here, and the agent told to stop
					if err := db.EndHeld(tx, tid, r.Target); err != nil {
						return nil, err
					}
					if err := db.RefreshSent(tx, tid); err != nil {
						return nil, err
					}
					ids = append(ids, r.ID)
					retired = append(retired, Retired{RID: r.ID, Requester: r.Requester, Target: r.Target, Held: true})
					continue
				}
				notify := r.Requester != human
				ok, err := db.CloseIfQueued(tx, r.ID, "rejected", why, db.CloseOpts{Announced: !notify, NoSettle: true})
				if err != nil {
					return nil, err
				}
				if !ok {
					failed = append(failed, fmt.Sprintf("#%d: was closed while this ran - nothing to retire", r.ID))
					continue
				}
				if err := addEvent(tx, r.Requester, "closed_under", kv{"request_id", r.ID}, kv{"target", r.Target}); err != nil {
					return nil, err
				}
				ids = append(ids, r.ID)
				retired = append(retired, Retired{RID: r.ID, Requester: r.Requester, Target: r.Target, Delivered: r.DeliveredAt.Valid, Notify: notify})
			}
			if bare { // its owner holds it on the founder's word or its own: the release ends that, and the owner is told to stop
				retired = append(retired, Retired{Target: t.Owner, Held: true})
			} else if len(ids) == 0 {
				return nil, refusal(fmt.Sprintf("task T%d released nothing: %s", tid, strings.Join(failed, "; ")))
			}
			payload := []kv{{"task_id", tid}, {"retired", ids}, {"reason", said}}
			if len(failed) > 0 {
				payload = append(payload, kv{"failed", failed})
				tail = " - not retired: " + strings.Join(failed, "; ")
			}
			if err := addEvent(tx, by, "release_task", payload...); err != nil {
				return nil, err
			}
			t, err = getTask(tx, tid)
			if err != nil {
				return nil, err
			}
			still, err := isDoing(tx, t)
			if err != nil {
				return nil, err
			}
			if still { // every direct request just closed: what is left is a child's
				child, err := childAsking(tx, tid)
				if err != nil {
					return nil, err
				}
				outcome = fmt.Sprintf("but it is still doing through its child T%d - release it there too; its own timeout keeps running until then", child)
			} else {
				if _, err := tx.Exec("UPDATE tasks SET timeout_at = NULL, timeout_left = NULL WHERE id = ?", tid); err != nil {
					return nil, err
				}
				back := "draft"
				if t.QueuedBy.Valid && t.QueuedBy.String != "" {
					back = "queued"
				}
				outcome = "back to " + back + ", its timeout paused"
			}
			stopped := map[string]bool{} // typed a stop by the Teller, which says the same
			for _, r := range retired {
				if r.Held {
					stopped[r.Target] = true
				}
			}
			var to []string
			for _, who := range []string{t.OpenedBy, t.Owner, human} {
				if !stopped[who] && (who != human || begun) {
					to = append(to, who)
				}
			}
			if notes, err = tellTask(tx, home, by, "task "+why, to...); err != nil {
				return nil, err
			}
			return accepted(fmt.Sprintf("task %s - %s%s", why, outcome, tail), nil), nil
		})
		if err == nil && tell != nil {
			// The stops typed, the requesters told and the queues moved up are the Teller's; it answers what it did, each in a phrase.
			if said := tell.Released(tid, why, retired); len(said) > 0 {
				out["said"] = fmt.Sprintf("task %s - %s, %s%s", why, strings.Join(said, "; "), outcome, tail)
			}
			tell.Notified(notes)
		}
		return out, err
	}
}

// childAsking is the first open child of tid with a request out on it, 0 when none.
func childAsking(tx *sql.Tx, tid int64) (int64, error) {
	kids, err := openChildren(tx, tid)
	if err != nil {
		return 0, err
	}
	for _, k := range kids {
		reqs, err := taskRequests(tx, k)
		if err != nil {
			return 0, err
		}
		for _, r := range reqs {
			if r.asks() {
				return k, nil
			}
		}
	}
	return 0, nil
}
