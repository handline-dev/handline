package db

import (
	"database/sql"
)

// The kinds of For you item the founder can defer (the later table).
const (
	LaterTask    = "task"
	LaterComment = "comment"
	LaterMachine = "machine"
)

// Later is one deferred item: it returns at Until, or when task After is done or discarded. Exactly one is set.
type Later struct {
	Kind, ID     string
	Until, After sql.NullInt64
	SetAt        int64
}

// LaterSet defers the item, replacing any earlier deferral of it.
func LaterSet(q Q, kind, id string, until, after *int64, now int64) error {
	_, err := q.Exec("INSERT INTO later(item_kind, item_id, until_at, after_task, set_at) VALUES(?, ?, ?, ?, ?) "+
		"ON CONFLICT(item_kind, item_id) DO UPDATE SET until_at = excluded.until_at, after_task = excluded.after_task, set_at = excluded.set_at",
		kind, id, until, after, now)
	return err
}

// LaterClear returns the item at once; false when it was not deferred.
func LaterClear(q Q, kind, id string) (bool, error) {
	res, err := q.Exec("DELETE FROM later WHERE item_kind = ? AND item_id = ?", kind, id)
	if err != nil {
		return false, err
	}
	n, err := res.RowsAffected()
	return n > 0, err
}

// LaterRows are every row of the table, oldest first.
func LaterRows(q Q) ([]Later, error) {
	rows, err := q.Query("SELECT item_kind, item_id, until_at, after_task, set_at FROM later ORDER BY set_at, item_kind, item_id")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Later
	for rows.Next() {
		var l Later
		if err := rows.Scan(&l.Kind, &l.ID, &l.Until, &l.After, &l.SetAt); err != nil {
			return nil, err
		}
		out = append(out, l)
	}
	return out, rows.Err()
}

// TaskEnd says how a task stands: "" when it is open, else "done" or "discarded"; found is false for a task that does not exist.
func TaskEnd(q Q, tid int64) (end string, found bool, err error) {
	var done, discarded sql.NullString
	switch err = q.QueryRow("SELECT done_evidence, discarded_by FROM tasks WHERE id = ?", tid).Scan(&done, &discarded); {
	case err == sql.ErrNoRows:
		return "", false, nil
	case err != nil:
		return "", false, err
	case done.Valid:
		return "done", true, nil
	case discarded.Valid:
		return "discarded", true, nil
	}
	return "", true, nil
}

// Over is why the deferral has ended at now: "time", "task_done" or "task_discarded"; "" while it holds. A task that is gone ends it as discarded.
func (l Later) Over(q Q, now int64) (string, error) {
	if l.Until.Valid {
		if now >= l.Until.Int64 {
			return "time", nil
		}
		return "", nil
	}
	end, found, err := TaskEnd(q, l.After.Int64)
	switch {
	case err != nil:
		return "", err
	case !found || end == "discarded":
		return "task_discarded", nil
	case end == "done":
		return "task_done", nil
	}
	return "", nil
}

// LaterOf is the deferral of the item while it holds at now, nil when the item is not deferred.
func LaterOf(q Q, kind, id string, now int64) (*Later, error) {
	var l Later
	switch err := q.QueryRow("SELECT item_kind, item_id, until_at, after_task, set_at FROM later WHERE item_kind = ? AND item_id = ?", kind, id).
		Scan(&l.Kind, &l.ID, &l.Until, &l.After, &l.SetAt); {
	case err == sql.ErrNoRows:
		return nil, nil
	case err != nil:
		return nil, err
	}
	over, err := l.Over(q, now)
	if err != nil || over != "" {
		return nil, err
	}
	return &l, nil
}

// CommentAskTask is the task a comment's open ask to the founder belongs to; false when the comment has no open ask.
func CommentAskTask(q Q, mid int64) (int64, bool, error) {
	var tid int64
	switch err := q.QueryRow("SELECT m.task_id FROM task_memos m JOIN requests r ON r.memo_id = m.id AND r.state = 'queued' WHERE m.id = ?", mid).Scan(&tid); {
	case err == sql.ErrNoRows:
		return 0, false, nil
	case err != nil:
		return 0, false, err
	}
	return tid, true, nil
}
