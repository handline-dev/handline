package taskwrites

import (
	"database/sql"
	"time"

	"github.com/handline/handline/go/internal/rpc"
)

// The board's facts, read in the transaction of the write that needs them (handline/actions/tasks.py).

// task is the columns of a task row the writes read.
type task struct {
	ID                                  int64
	Owner, OpenedBy                     string
	DoneEvidence, DiscardedBy, QueuedBy sql.NullString
	SentAt                              sql.NullInt64
	TimeoutS                            sql.NullInt64
	Parent                              sql.NullInt64
}

func getTask(tx *sql.Tx, tid int64) (*task, error) {
	t := &task{ID: tid}
	err := tx.QueryRow("SELECT owner, opened_by, done_evidence, discarded_by, queued_by, sent_at, timeout_s, parent_task FROM tasks WHERE id = ?", tid).
		Scan(&t.Owner, &t.OpenedBy, &t.DoneEvidence, &t.DiscardedBy, &t.QueuedBy, &t.SentAt, &t.TimeoutS, &t.Parent)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	return t, err
}

func (t *task) done() bool { return t.DoneEvidence.Valid && t.DoneEvidence.String != "" }

// asker is who the task answers to when it closes: the founder if any request linked to it came from the founder,
// else whoever opened it (task_asker).
func asker(tx *sql.Tx, t *task) (string, error) {
	var n int
	err := tx.QueryRow("SELECT COUNT(*) FROM requests r JOIN task_requests l ON l.request_id = r.id WHERE l.task_id = ? AND r.requester = ?", t.ID, human).Scan(&n)
	if err != nil {
		return "", err
	}
	if n > 0 {
		return human, nil
	}
	return t.OpenedBy, nil
}

const eventTask = "CASE WHEN json_valid(payload) THEN json_extract(payload, '$.task_id') END"

// isDoing is reads_doing and holds_current: sent, or an open child sent, or begun on a founder message; and the one its owner holds.
func isDoing(tx *sql.Tx, t *task) (bool, error) {
	var n int
	if err := tx.QueryRow("SELECT COUNT(*) FROM events WHERE kind = 'task_begin' AND "+eventTask+" = ?", t.ID).Scan(&n); err != nil {
		return false, err
	}
	reads := n > 0 || t.SentAt.Valid
	if !reads {
		if err := tx.QueryRow("SELECT COUNT(*) FROM tasks WHERE parent_task = ? AND done_evidence IS NULL AND discarded_by IS NULL AND sent_at IS NOT NULL", t.ID).Scan(&n); err != nil {
			return false, err
		}
		reads = n > 0
	}
	if !reads {
		return false, nil
	}
	if t.Owner == human {
		return true, nil
	}
	var held sql.NullInt64
	err := tx.QueryRow("SELECT current_task FROM agent_state WHERE agent = ?", t.Owner).Scan(&held)
	if err != nil && err != sql.ErrNoRows {
		return false, err
	}
	if !held.Valid || held.Int64 == t.ID {
		return true, nil
	}
	cur, err := getTask(tx, held.Int64)
	if err != nil {
		return false, err
	}
	return cur == nil || cur.done() || cur.DiscardedBy.Valid && cur.DiscardedBy.String != "", nil
}

// refreshSent sets tasks.sent_at (once) while a request that asks something is out on the task, and clears it when none is left.
func refreshSent(tx *sql.Tx, tid int64) error {
	_, err := tx.Exec("UPDATE tasks SET sent_at = CASE WHEN EXISTS "+
		"(SELECT 1 FROM task_requests l JOIN requests r ON r.id = l.request_id WHERE l.task_id = tasks.id "+
		"AND (r.state = 'queued' OR (r.state = 'landed' AND r.kind = 'task' "+
		"AND r.target = tasks.owner AND r.queue_order IS NOT NULL))) THEN COALESCE(sent_at, ?) ELSE NULL END WHERE id = ?",
		time.Now().Unix(), tid)
	return err
}

func newestRequest(tx *sql.Tx) (int64, error) {
	var n int64
	err := tx.QueryRow("SELECT COALESCE(MAX(id), 0) FROM requests").Scan(&n)
	return n, err
}

// addEvent appends an event as db.add_event does.
func addEvent(tx *sql.Tx, agent, kind string, payload map[string]any) error {
	var p any
	if len(payload) > 0 {
		p = rpc.PyJSON(payload, false, false)
	}
	_, err := tx.Exec("INSERT INTO events(ts, agent, kind, payload) VALUES(?,?,?,?)", time.Now().Unix(), agent, kind, p)
	return err
}
