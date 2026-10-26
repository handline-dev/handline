package db

import (
	"database/sql"
	"time"

	"github.com/handline/handline/go/internal/policy"
)

// Watch is what a gate task watches: Target of Vendor (an outside tool, package watch), waiting for Until, said as Label ("PR #418 CI").
// Result is what it opened with, "" while it watches: passed, failed or timed_out. Error is why its last read failed, "" when it did not.
type Watch struct {
	Vendor, Target, Until, Label string
	Result, Error                string
	Since                        int64 // when the gate started watching: its creation
}

// GateDeadlineS is a gate's time box when none is given (policy.GateDeadline).
const GateDeadlineS = int64(policy.GateDeadline / time.Second)

// OpenGate makes task tid, owned by Gate, watch w: it is queued by `by` and doing from now, and its deadline is its time box from now.
func OpenGate(q Q, tid int64, by string, w Watch) error {
	now := Now()
	_, err := q.Exec("UPDATE tasks SET watch_vendor = ?, watch_target = ?, watch_until = ?, watch_label = ?, queued_by = COALESCE(queued_by, ?), "+
		"queued_at = COALESCE(queued_at, ?), sent_at = ?, timeout_at = ? + COALESCE(timeout_s, ?) WHERE id = ?",
		w.Vendor, w.Target, w.Until, w.Label, by, now, now, now, GateDeadlineS, tid)
	return err
}

const watchCols = "watch_vendor, watch_target, watch_until, watch_label, COALESCE(watch_result, ''), COALESCE(watch_error, ''), created_at"

func (w *Watch) dest() []any {
	return []any{&w.Vendor, &w.Target, &w.Until, &w.Label, &w.Result, &w.Error, &w.Since}
}

// TaskWatch is what task tid watches, nil for a task that is no gate.
func TaskWatch(q Q, tid int64) (*Watch, error) {
	var w Watch
	err := q.QueryRow("SELECT "+watchCols+" FROM tasks WHERE id = ? AND watch_vendor IS NOT NULL", tid).Scan(w.dest()...)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &w, nil
}

// OpenGates are the gates still watching, oldest first, with their deadline (timeout_at, 0 for none).
func OpenGates(q Q) (ids []int64, watches []Watch, deadlines []int64, err error) {
	rows, err := q.Query("SELECT id, "+watchCols+", COALESCE(timeout_at, 0) FROM tasks WHERE owner = ? AND watch_vendor IS NOT NULL "+
		"AND done_evidence IS NULL AND discarded_by IS NULL ORDER BY id", Gate)
	if err != nil {
		return nil, nil, nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var id, deadline int64
		var w Watch
		if err := rows.Scan(append(append([]any{&id}, w.dest()...), &deadline)...); err != nil {
			return nil, nil, nil, err
		}
		ids, watches, deadlines = append(ids, id), append(watches, w), append(deadlines, deadline)
	}
	return ids, watches, deadlines, rows.Err()
}

// SetWatchResult keeps what the gate opened with.
func SetWatchResult(q Q, tid int64, result string) error {
	_, err := q.Exec("UPDATE tasks SET watch_result = ?, watch_error = NULL WHERE id = ?", result, tid)
	return err
}

// SetWatchError keeps why the gate's last read failed; "" clears it.
func SetWatchError(q Q, tid int64, why string) error {
	_, err := q.Exec("UPDATE tasks SET watch_error = NULLIF(?, '') WHERE id = ?", why, tid)
	return err
}
