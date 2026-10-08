package taskwrites

import (
	"database/sql"
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
)

// Retired is a request a release ended: held ones (the owner's landed delivery) are typed a stop, queued ones are closed as rejected.
type Retired struct {
	RID               int64
	Requester, Target string
	Held, Delivered   bool
	Notify            bool // the requester is told of the close
}

// releaseMethod is task.release (actions.release_task): the opener's or the founder's call to take a doing task back to queued or draft.
func releaseMethod(tell Teller) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		for _, k := range []string{"to", "reason"} {
			if p[k] != nil {
				return nil, badParam(k+" is not served yet", k)
			}
		}
		tid, err := taskID(p)
		if err != nil {
			return nil, err
		}
		by := c.Caller.Actor()
		why := fmt.Sprintf("T%d released by %s", tid, by)
		var (
			retired       []Retired
			outcome, tail string
		)
		out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			retired, outcome, tail = nil, "", ""
			t, err := getTask(tx, tid)
			if err != nil {
				return nil, err
			}
			if t == nil {
				return nil, refusal(fmt.Sprintf("no task T%d", tid))
			}
			if g := editGuard(&mergeRow{ID: tid, OpenedBy: t.OpenedBy}, by); g != "" {
				return nil, refusal(g)
			}
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
			if len(direct) == 0 {
				if child, err := childAsking(tx, tid); err != nil {
					return nil, err
				} else if child != 0 {
					return nil, refusal(fmt.Sprintf("task T%d is doing through its child T%d - release it there", tid, child))
				}
				return nil, refusal(fmt.Sprintf("task T%d was begun on a founder message: it is doing until it is done", tid))
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
			if len(ids) == 0 {
				return nil, refusal(fmt.Sprintf("task T%d released nothing: %s", tid, strings.Join(failed, "; ")))
			}
			payload := []kv{{"task_id", tid}, {"retired", ids}}
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
			if still { // every direct request just closed: what is left is a child's, or a founder-begun task
				child, err := childAsking(tx, tid)
				if err != nil {
					return nil, err
				}
				outcome = "but it is still doing because it was begun on a founder message"
				if child != 0 {
					outcome = fmt.Sprintf("but it is still doing through its child T%d - release it there too", child)
				}
				outcome += "; its own timeout keeps running until then"
			} else {
				if _, err := tx.Exec("UPDATE tasks SET timeout_at = NULL WHERE id = ? AND timeout_at IS NOT NULL", tid); err != nil {
					return nil, err
				}
				back := "draft"
				if t.QueuedBy.Valid && t.QueuedBy.String != "" {
					back = "queued"
				}
				outcome = "back to " + back + ", its timeout paused"
			}
			return accepted(fmt.Sprintf("task T%d released by %s: %s%s", tid, by, outcome, tail), nil), nil
		})
		if err == nil && tell != nil && len(retired) > 0 {
			// The stops typed, the requesters told and the queues moved up are the Teller's; it answers what it did, each in a phrase.
			if said := tell.Released(tid, why, retired); len(said) > 0 {
				out["said"] = fmt.Sprintf("task T%d released by %s: %s, %s%s", tid, by, strings.Join(said, "; "), outcome, tail)
			}
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
