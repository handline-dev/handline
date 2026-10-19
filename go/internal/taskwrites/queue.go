package taskwrites

import (
	"database/sql"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/gitrun"
	"github.com/handline/handline/go/internal/handoff"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/textsafe"
)

// queueMethod is task.queue (approve drafts to run) and task.draft (send queued or doing ones back): anyone's call, all the named tasks
// changing or none (actions.set_queued_all), each task's opener and assignee told.
func queueMethod(home string, tell Teller, queued bool) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		tids, err := taskIDs(p, "tasks")
		if err != nil {
			return nil, err
		}
		by := c.Caller.Actor()
		kept := map[int64]string{} // read before the write: git is not run with the write held
		if !queued {
			for _, tid := range tids {
				if kept[tid], err = workKept(c.Server().DB, tid); err != nil {
					return nil, err
				}
			}
		}
		var (
			done  []int64
			notes []told
			set   map[int64]queuedSet
		)
		out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			done, notes, set = nil, nil, map[int64]queuedSet{}
			var said []string
			seen := map[int64]bool{}
			for _, tid := range tids {
				if seen[tid] {
					continue
				}
				seen[tid] = true
				r, err := setQueued(tx, home, tid, by, queued, kept[tid])
				if err != nil {
					return nil, err
				}
				said, notes = append(said, r.msg+r.memo), append(notes, r.notes...)
				set[tid] = r
				done = append(done, tid)
			}
			return accepted(strings.Join(said, "\n"), nil), nil
		})
		if err == nil && tell != nil && len(done) > 0 {
			// Queuing also delivers: the task goes out now if its owner is free. The reply says what happened to each; a replay of this call
			// is answered with what was kept, which says only that the tasks are queued.
			delivered := make([]string, len(done))
			for i, tid := range done {
				delivered[i] = tell.QueueDelivered(tid, by, queued) + set[tid].memo
				if closed := set[tid].closed; len(closed) > 0 {
					delivered[i] += " - " + strings.Join(tell.Released(tid, draftWhy(tid, by), closed), "; ")
				}
			}
			out["said"] = strings.Join(delivered, "\n")
			tell.Notified(notes)
		}
		return out, err
	}
}

func taskIDs(p map[string]any, key string) ([]int64, error) {
	list, ok := p[key].([]any)
	if !ok || len(list) == 0 {
		return nil, badParam(key+" is a list of task numbers", key)
	}
	out := make([]int64, len(list))
	for i, v := range list {
		n, ok := rpc.AsInt(v)
		if !ok {
			return nil, badParam(key+" is a list of task numbers", key)
		}
		out[i] = n
	}
	return out, nil
}

func draftWhy(tid int64, by string) string { return fmt.Sprintf("T%d made draft by %s", tid, by) }

// queuedSet is what setQueued did to one task: its message, the phrase naming the memo a park left ("" for none), the notices of it, and
// the delivery requests a draft withdrew.
type queuedSet struct {
	msg, memo string
	notes     []told
	closed    []Retired
}

// setQueued is _queue_state: the checks and the write (nobody works a draft). kept is the memo a draft leaves when it parks a holder with
// work in its worktree.
func setQueued(tx *sql.Tx, home string, tid int64, by string, queued bool, kept string) (queuedSet, error) {
	t, err := getTask(tx, tid)
	if err != nil {
		return queuedSet{}, err
	}
	if t == nil {
		return queuedSet{}, refusal(fmt.Sprintf("no task T%d", tid))
	}
	st, err := state(tx, t)
	if err != nil {
		return queuedSet{}, err
	}
	want, other := "draft", "queued"
	if queued {
		want, other = "queued", "draft"
	}
	if st == want {
		return queuedSet{}, refusal(fmt.Sprintf("task T%d is already %s", tid, want))
	}
	if st != other && (queued || st != "doing") {
		only := other
		if !queued {
			only = "queued or doing"
		}
		return queuedSet{}, refusal(fmt.Sprintf("task T%d is %s - only a %s task can be made %s", tid, st, only, want))
	}
	var (
		res    sql.Result
		closed []Retired
	)
	if queued {
		res, err = tx.Exec("UPDATE tasks SET queued_by = ?, queued_at = ? WHERE id = ? AND "+openSQL+" AND queued_by IS NULL", by, time.Now().Unix(), tid)
	} else {
		if closed, err = withdrawDelivery(tx, t, by); err != nil {
			return queuedSet{}, err
		}
		res, err = tx.Exec("UPDATE tasks SET queued_by = NULL, queued_at = NULL WHERE id = ? AND "+openSQL, tid)
	}
	if err != nil {
		return queuedSet{}, err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return queuedSet{}, refusal(fmt.Sprintf("task T%d changed under this call - read it again", tid))
	}
	if !queued {
		if t, err = getTask(tx, tid); err != nil {
			return queuedSet{}, err
		}
		if st, err := state(tx, t); err != nil {
			return queuedSet{}, err
		} else if st != "draft" {
			why := fmt.Sprintf("task T%d is still %s", tid, st)
			if child, err := childAsking(tx, tid); err != nil {
				return queuedSet{}, err
			} else if child != 0 {
				why += fmt.Sprintf(": its child T%d is out - make that draft first", child)
			} else if bare, err := startedBare(tx, tid); err != nil {
				return queuedSet{}, err
			} else if bare {
				why += ": it was begun without a delivery - release it instead"
			}
			return queuedSet{}, refusal(why)
		}
	}
	parked := ""
	for _, r := range closed {
		if r.Park {
			parked = r.Target
		}
	}
	after, err := newestRequest(tx)
	if err != nil {
		return queuedSet{}, err
	}
	kind, word := "draft_task", "draft"
	if queued {
		kind, word = "queue_task", "queued"
	}
	if err := addEvent(tx, by, kind, kv{"task_id", tid}, kv{"after_request", after}); err != nil {
		return queuedSet{}, err
	}
	msg, memo := fmt.Sprintf("task T%d is %s (%s)", tid, word, by), ""
	if parked != "" {
		if _, err := tx.Exec("UPDATE tasks SET timeout_at = NULL, timeout_left = NULL WHERE id = ?", tid); err != nil {
			return queuedSet{}, err
		}
		if kept != "" {
			mid, err := db.AddMemo(tx, tid, by, kept)
			if err != nil {
				return queuedSet{}, err
			}
			memo = fmt.Sprintf(", memo %d keeps where %s stopped", mid, parked)
		}
	}
	var to []string
	for _, who := range []string{t.OpenedBy, t.Owner} {
		if who != parked { // typed the stop by the Teller instead
			to = append(to, who)
		}
	}
	notes, err := tellTask(tx, home, by, msg+memo, to...)
	return queuedSet{msg, memo, notes, closed}, err
}

// withdrawDelivery closes the requests still asking t's owner to work it (stored withdrawn when not yet delivered, else landed with the
// draft as its verdict), nobody told of the close (the draft notice tells the opener and the owner). One that has landed is ended; when
// its target is working the task it is parked: typed to stop and moved on to its next task.
func withdrawDelivery(tx *sql.Tx, t *task, by string) ([]Retired, error) {
	reqs, err := taskRequests(tx, t.ID)
	if err != nil {
		return nil, err
	}
	var closed []Retired
	ended := false
	for _, r := range reqs {
		if !r.asks() {
			continue
		}
		if r.State == "landed" {
			if err := db.EndHeld(tx, t.ID, r.Target); err != nil {
				return nil, err
			}
			ended = true
			working, err := worksOn(tx, r.Target, t.ID)
			if err != nil {
				return nil, err
			}
			if working {
				closed = append(closed, Retired{RID: r.ID, Requester: r.Requester, Target: r.Target, Held: true, Park: true})
			}
			continue
		}
		ok, err := db.CloseIfQueued(tx, r.ID, "rejected", draftWhy(t.ID, by))
		if err != nil {
			return nil, err
		}
		if !ok {
			return nil, refusal(fmt.Sprintf("task T%d changed under this call - read it again", t.ID))
		}
		if err := addEvent(tx, r.Requester, "closed_under", kv{"request_id", r.ID}, kv{"target", r.Target}); err != nil {
			return nil, err
		}
		closed = append(closed, Retired{RID: r.ID, Requester: r.Requester, Target: r.Target, Delivered: r.DeliveredAt.Valid})
	}
	if len(closed) > 0 || ended {
		return closed, db.RefreshSent(tx, t.ID)
	}
	return nil, nil
}

// worksOn is whether agent's current task is tid (the founder holds no current task).
func worksOn(tx *sql.Tx, agent string, tid int64) (bool, error) {
	if agent == human {
		return false, nil
	}
	var cur sql.NullInt64
	err := tx.QueryRow("SELECT current_task FROM agent_state WHERE agent = ?", agent).Scan(&cur)
	if err == sql.ErrNoRows {
		return false, nil
	}
	return cur.Valid && cur.Int64 == tid, err
}

// workKept is the memo a draft leaves on tid when the agent working it has something to resume from: commits on its worktree's branch
// that main lacks, made since the task landed, or uncommitted changes. "" when there is none, or no worktree to read.
func workKept(q *sql.DB, tid int64) (string, error) {
	var agent, recorded string
	var landed int64
	err := q.QueryRow("SELECT r.target, COALESCE(r.landed_at, 0), COALESCE(a.worktree, '') FROM requests r "+
		"JOIN task_requests l ON l.request_id = r.id JOIN agent_state a ON a.agent = r.target "+
		"WHERE l.task_id = ? AND r.state = 'landed' AND r.kind = 'task' AND r.queue_order IS NOT NULL AND a.current_task = ? "+
		"ORDER BY r.id DESC LIMIT 1", tid, tid).Scan(&agent, &landed, &recorded)
	if err == sql.ErrNoRows {
		return "", nil
	} else if err != nil {
		return "", err
	}
	wt := handoff.WorktreePath(recorded)
	if wt == "" {
		return "", nil
	}
	branch := gitrun.Run(wt, "rev-parse", "--abbrev-ref", "HEAD")
	head := gitrun.Run(wt, "rev-parse", "--short", "HEAD")
	log := gitrun.Run(wt, "log", "--format=%ct", gitrun.IntegrationBranch+"..HEAD")
	dirty := gitrun.Run(wt, "status", "--porcelain")
	if branch.RC != 0 || head.RC != 0 || log.RC != 0 || dirty.RC != 0 {
		return "", nil
	}
	commits := 0
	for _, ct := range strings.Fields(log.Stdout) {
		if n, _ := strconv.ParseInt(ct, 10, 64); n >= landed {
			commits++
		}
	}
	changed := strings.TrimSpace(dirty.Stdout) != ""
	if commits == 0 && !changed {
		return "", nil
	}
	var has []string
	if commits > 0 {
		has = append(has, fmt.Sprintf("%d commit(s) since it landed", commits))
	}
	if changed {
		has = append(has, "uncommitted changes")
	}
	return fmt.Sprintf("parked while %s worked it: branch %s @ %s in %s (%s) - resume from there", agent,
		textsafe.Oneline(strings.TrimSpace(branch.Stdout)), strings.TrimSpace(head.Stdout), wt, strings.Join(has, ", ")), nil
}
