package db

import (
	"database/sql"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/handline/handline/go/internal/textsafe"
)

// What a task is, read off the rows (actions/tasks.py: task_state and the facts it is built from). The one copy the supervisor uses.

// Asks is actions._asks: a linked request that is work still being asked for. A report to the founder is a say, born landed, so
// linking one never makes a task doing.
func Asks(r *Request) bool {
	return r.State == "queued" || (r.State == Landed && r.Kind.String == "task" && r.QueueOrder.Valid)
}

// Begun is actions._begun: the task was begun on a founder's message (task_begin).
func Begun(q Q, tid int64) (bool, error) {
	var one int
	err := q.QueryRow("SELECT 1 FROM events WHERE kind = 'task_begin' AND "+eventTask+" = ? LIMIT 1", tid).Scan(&one)
	if err == sql.ErrNoRows {
		return false, nil
	}
	return err == nil, err
}

// ReadsDoing is actions.reads_doing: the task has been sent to its owner, or an open child of it has, or it was begun on a founder message.
func ReadsDoing(q Q, tid int64) (bool, error) {
	t, err := GetTask(q, tid)
	if err != nil || t == nil {
		return false, err
	}
	if b, err := Begun(q, tid); b || err != nil {
		return b, err
	}
	if t.SentAt.Valid {
		return true, nil
	}
	kids, err := OpenChildTasks(q, tid)
	if err != nil {
		return false, err
	}
	for _, k := range kids {
		if k.SentAt.Valid {
			return true, nil
		}
	}
	return false, nil
}

// HoldsCurrent is actions.holds_current: the task is the one its owner holds (an agent holds one at a time).
func HoldsCurrent(q Q, t *Task) (bool, error) {
	if t.Owner == Human {
		return true, nil
	}
	held, ok, err := CurrentTask(q, t.Owner)
	if err != nil {
		return false, err
	}
	if !ok || held == t.ID {
		return true, nil
	}
	cur, err := GetTask(q, held)
	if err != nil {
		return false, err
	}
	return cur == nil || cur.Ended(), nil
}

// IsDoing is actions.is_doing: reads_doing and the one its owner holds.
func IsDoing(q Q, tid int64) (bool, error) {
	t, err := GetTask(q, tid)
	if err != nil || t == nil {
		return false, err
	}
	if d, err := ReadsDoing(q, tid); !d || err != nil {
		return false, err
	}
	return HoldsCurrent(q, t)
}

func stateOf(t *Task, doing, asked bool) string {
	switch {
	case t.DiscardedBy.String != "":
		return "discarded"
	case t.DoneEvidence.String != "":
		return "done"
	case doing:
		return "doing"
	case t.QueuedBy.Valid && t.QueuedBy.String != "" || asked:
		return "queued"
	}
	return "draft"
}

// TaskState is actions.task_state: draft, queued, doing, done or discarded.
func TaskState(q Q, t *Task) (string, error) {
	if t.Ended() {
		return stateOf(t, false, false), nil
	}
	asked, err := ReadsDoing(q, t.ID)
	if err != nil {
		return "", err
	}
	holds := false
	if asked {
		if holds, err = HoldsCurrent(q, t); err != nil {
			return "", err
		}
	}
	return stateOf(t, asked && holds, asked), nil
}

// ChildBlockerOf is actions.child_blocker_of: the open child task of someone else that blocks tid, nil for none.
func ChildBlockerOf(q Q, tid int64) (*Task, error) {
	t, err := GetTask(q, tid)
	if err != nil || t == nil {
		return nil, err
	}
	blockers, err := TaskBlockers(q, tid)
	if err != nil {
		return nil, err
	}
	var children []*Task
	for _, b := range blockers {
		if b.ParentTask.Valid && b.ParentTask.Int64 == tid {
			children = append(children, b)
		}
	}
	for _, b := range children {
		if b.Owner != t.Owner {
			continue
		}
		st, err := TaskState(q, b)
		if err != nil {
			return nil, err
		}
		if st == "queued" || st == "doing" {
			return nil, nil
		}
	}
	for _, b := range children {
		if b.Owner != t.Owner {
			return b, nil
		}
	}
	return nil, nil
}

func currentOpen(q Q, agent string) (*Task, error) {
	tid, ok, err := CurrentTask(q, agent)
	if err != nil || !ok {
		return nil, err
	}
	t, err := GetTask(q, tid)
	if err != nil || t == nil || t.Ended() {
		return nil, err
	}
	return t, nil
}

// ChildWait is actions.child_wait: what the agent waits on when its current task is held by a child of its own ("waiting for T<n> (owner)").
func ChildWait(q Q, agent string) (string, error) {
	t, err := currentOpen(q, agent)
	if err != nil || t == nil {
		return "", err
	}
	child, err := ChildBlockerOf(q, t.ID)
	if err != nil || child == nil {
		return "", err
	}
	return "waiting for T" + itoa(child.ID) + " (" + child.Owner + ")", nil
}

// BlockWait is actions.block_wait: what the agent is held up by when its current task is blocked ("blocked by T<n> (owner), ...").
func BlockWait(q Q, agent string) (string, error) {
	t, err := currentOpen(q, agent)
	if err != nil || t == nil {
		return "", err
	}
	bs, err := TaskBlockers(q, t.ID)
	if err != nil || len(bs) == 0 {
		return "", err
	}
	parts := make([]string, len(bs))
	for i, b := range bs {
		parts[i] = "T" + itoa(b.ID) + " (" + b.Owner + ")"
	}
	return "blocked by " + strings.Join(parts, ", "), nil
}

// CurrentTaskStillAsking is actions._current_task_still_asking: the agent's current task still has its own request out, the one thing left
// to deliver. A begun task always does, until it is done; an owner waiting on a child is held on it.
func CurrentTaskStillAsking(q Q, agent string) (bool, error) {
	t, err := currentOpen(q, agent)
	if err != nil || t == nil {
		return false, err
	}
	if b, err := Begun(q, t.ID); b || err != nil {
		return b, err
	}
	if child, err := ChildBlockerOf(q, t.ID); child != nil || err != nil {
		return child != nil, err
	}
	return t.SentAt.Valid, nil
}

// NewestRequest is actions._newest_request: the after_request a reset event carries.
func NewestRequest(q Q) (int64, error) {
	var n int64
	err := q.QueryRow("SELECT COALESCE(MAX(id), 0) FROM requests").Scan(&n)
	return n, err
}

// resetEvents are the events after which a task's earlier answers no longer mean it was handled.
var resetEvents = []string{"reopen_task", "unblock_task", "task_unblocked", "queue_task"}

// WaitingTask is a task that could become current and its own queued request, nil when it was never delivered.
type WaitingTask struct {
	Task *Task
	Req  *Request
}

// WaitingTasks is actions._waiting_tasks: the agent's open tasks that could become current, by priority then age. Skips one blocked,
// still a draft, or with nothing of its own left to deliver.
func WaitingTasks(q Q, agent string) ([]WaitingTask, error) {
	open, err := OpenTasks(q)
	if err != nil {
		return nil, err
	}
	var out []WaitingTask
	for _, t := range open {
		if t.Owner != agent || t.DoneEvidence.String != "" {
			continue
		}
		bs, err := TaskBlockers(q, t.ID)
		if err != nil {
			return nil, err
		}
		if len(bs) > 0 {
			continue
		}
		sent := t.SentAt.Valid
		linked, err := TaskRequests(q, t.ID)
		if err != nil {
			return nil, err
		}
		var req *Request
		for _, r := range linked {
			if Asks(r) {
				req = r
				break
			}
		}
		var after int64
		if !sent {
			if after, err = ResetAfter(q, t.ID); err != nil {
				return nil, err
			}
			delivered := false
			for _, r := range linked {
				if r.Target == agent && r.ID > after && !neverReached(r.State) {
					delivered = true
				}
			}
			if delivered {
				continue // delivered and answered since it last came due: not a reason to send it again
			}
		}
		if sent || t.QueuedBy.Valid {
			out = append(out, WaitingTask{t, req})
		}
	}
	return out, nil
}

// ResetAfter is actions._reset_after: the highest request id a reset event on the task has covered.
func ResetAfter(q Q, tid int64) (int64, error) {
	args := make([]any, 0, len(resetEvents)+1)
	for _, k := range resetEvents {
		args = append(args, k)
	}
	args = append(args, tid)
	var n int64
	err := q.QueryRow("SELECT COALESCE(MAX(CAST(json_extract(payload, '$.after_request') AS INTEGER)), 0) FROM events WHERE kind IN ("+marks(len(resetEvents))+") AND "+eventTask+" = ?", args...).Scan(&n)
	return n, err
}

func neverReached(state string) bool {
	for _, s := range neverReachedTarget {
		if s == state {
			return true
		}
	}
	return false
}

// MemoSummary is actions.memo_summary: the task's memos in one short line for a delivery or a start; "" when it has none.
func MemoSummary(q Q, tid int64, cap int) (string, error) {
	rows, err := q.Query("SELECT author, body, created_at, edited_at FROM task_memos WHERE task_id = ? ORDER BY id", tid)
	if err != nil {
		return "", err
	}
	defer rows.Close()
	var parts []string
	for rows.Next() {
		var author, body string
		var created int64
		var edited sql.NullInt64
		if err := rows.Scan(&author, &body, &created, &edited); err != nil {
			return "", err
		}
		at := created
		if edited.Valid && edited.Int64 != 0 {
			at = edited.Int64
		}
		line := textsafe.Oneline(body)
		if utf8.RuneCountInString(line) > 160 {
			line = string([]rune(line)[:160])
		}
		parts = append(parts, author+" "+time.Unix(at, 0).Format("01-02 15:04")+": "+line)
	}
	if err := rows.Err(); err != nil || len(parts) == 0 {
		return "", err
	}
	text := "Memos - " + strings.Join(parts, " | ")
	if utf8.RuneCountInString(text) <= cap {
		return text, nil
	}
	return strings.TrimRight(string([]rune(text)[:cap]), " \t\n\r") + "…", nil
}
