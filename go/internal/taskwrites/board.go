package taskwrites

import (
	"database/sql"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
)

// The board's facts, read in the transaction of the write that needs them (handline/actions/tasks.py).

// task is the columns of a task row the writes read.
type task struct {
	ID                                  int64
	Owner, OpenedBy, Title              string
	DoneEvidence, DiscardedBy, QueuedBy sql.NullString
	Reporter, Report, ReportVia         sql.NullString
	SentAt, ReportedAt                  sql.NullInt64
	TimeoutS                            sql.NullInt64
	Parent                              sql.NullInt64
}

func getTask(tx *sql.Tx, tid int64) (*task, error) {
	t := &task{ID: tid}
	err := tx.QueryRow("SELECT owner, opened_by, title, done_evidence, discarded_by, queued_by, sent_at, timeout_s, parent_task, reporter, "+
		"reported_at, report, report_via FROM tasks WHERE id = ?", tid).
		Scan(&t.Owner, &t.OpenedBy, &t.Title, &t.DoneEvidence, &t.DiscardedBy, &t.QueuedBy, &t.SentAt, &t.TimeoutS, &t.Parent, &t.Reporter,
			&t.ReportedAt, &t.Report, &t.ReportVia)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	return t, err
}

func (t *task) done() bool { return t.DoneEvidence.Valid && t.DoneEvidence.String != "" }

// reported is whether its assignee's done waits on the reporter's accept or reject.
func (t *task) reported() bool {
	return t.ReportedAt.Valid && !t.done() && !(t.DiscardedBy.Valid && t.DiscardedBy.String != "")
}

// reporter is db.task_reporter: who the task answers to, its opener when none was set.
func (t *task) reporter() string {
	if t.Reporter.String != "" {
		return t.Reporter.String
	}
	return t.OpenedBy
}

// asker is who the task answers to when it closes: its reporter when one was set; else the founder if any request linked to
// it came from the founder, else whoever opened it (task_asker).
func asker(tx *sql.Tx, t *task) (string, error) {
	if t.Reporter.String != "" {
		return t.Reporter.String, nil
	}
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

// startedBare is the task begun on a founder message or started by its owner as its own, with no request, and not released since.
func startedBare(tx *sql.Tx, tid int64) (bool, error) {
	var n int
	err := tx.QueryRow("SELECT COUNT(*) FROM events WHERE kind IN ('task_begin', 'task_self_start') AND "+eventTask+" = ?"+db.SinceRelease("?"), tid, tid).Scan(&n)
	return n > 0, err
}

// readsDoing is reads_doing: the task was started bare, or a request is out on it or on an open child of it.
func readsDoing(tx *sql.Tx, t *task) (bool, error) {
	if bare, err := startedBare(tx, t.ID); bare || err != nil {
		return bare, err
	}
	if t.SentAt.Valid {
		return true, nil
	}
	var n int
	if err := tx.QueryRow("SELECT COUNT(*) FROM tasks WHERE parent_task = ? AND done_evidence IS NULL AND discarded_by IS NULL AND sent_at IS NOT NULL", t.ID).Scan(&n); err != nil {
		return false, err
	}
	return n > 0, nil
}

// holdsCurrent is holds_current: the task is the one its owner holds, or its owner holds none (or one that has ended).
func holdsCurrent(tx *sql.Tx, t *task) (bool, error) {
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
	return cur == nil || cur.done() || cur.reported() || cur.DiscardedBy.Valid && cur.DiscardedBy.String != "", nil
}

// isDoing is reads_doing and holds_current: sent, or an open child sent, or begun on a founder message; and the one its owner holds.
func isDoing(tx *sql.Tx, t *task) (bool, error) {
	reads, err := readsDoing(tx, t)
	if err != nil || !reads {
		return false, err
	}
	return holdsCurrent(tx, t)
}

// state is task_state: discarded, done, reported, doing (asked and held by its owner), else queued (approved, or asked but held back) or
// draft.
func state(tx *sql.Tx, t *task) (string, error) {
	switch {
	case t.DiscardedBy.Valid && t.DiscardedBy.String != "":
		return "discarded", nil
	case t.done():
		return "done", nil
	case t.reported():
		return "reported", nil
	}
	asked, err := readsDoing(tx, t)
	if err != nil {
		return "", err
	}
	doing := false
	if asked {
		if doing, err = holdsCurrent(tx, t); err != nil {
			return "", err
		}
	}
	switch {
	case doing:
		return "doing", nil
	case t.QueuedBy.Valid && t.QueuedBy.String != "" || asked:
		return "queued", nil
	}
	return "draft", nil
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

// kv is one key and value of an event payload: the keys go out in the order given, as a Python dict's insertion order does.
type kv struct {
	K string
	V any
}

// addEvent appends an event as db.add_event does, its payload written in the order of `payload`.
func addEvent(tx *sql.Tx, agent, kind string, payload ...kv) error {
	var p any
	if len(payload) > 0 {
		parts := make([]string, len(payload))
		for i, e := range payload {
			parts[i] = rpc.PyJSON(e.K, false, false) + ": " + rpc.PyJSON(e.V, false, false)
		}
		p = "{" + strings.Join(parts, ", ") + "}"
	}
	_, err := tx.Exec("INSERT INTO events(ts, agent, kind, payload) VALUES(?,?,?,?)", time.Now().Unix(), agent, kind, p)
	return err
}
