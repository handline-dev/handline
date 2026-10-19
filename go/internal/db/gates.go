package db

import (
	"database/sql"
	"fmt"
	"regexp"
)

// Watch is what a gate task watches: a GitHub PR of Repo (owner/name), On "ci" (every check has concluded, or the PR closed first) or
// "close" (merged or closed unmerged). Result is what it opened with, "" while it watches: passed, failed, merged, closed or timed_out.
type Watch struct {
	Repo   string
	PR     int64
	On     string
	Result string
}

var repoName = regexp.MustCompile(`^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$`)

// WatchRefusal is why a watch is not one, "" when it is.
func WatchRefusal(w Watch) string {
	switch {
	case !repoName.MatchString(w.Repo):
		return "watch.repo is a GitHub repo, owner/name"
	case w.PR <= 0:
		return "watch.pr is a pull request number"
	case w.On != "ci" && w.On != "close":
		return "watch.on is ci or close"
	}
	return ""
}

// Said is the watch in words: "PR #12 CI" or "PR #12 to close".
func (w Watch) Said() string {
	if w.On == "ci" {
		return fmt.Sprintf("PR #%d CI", w.PR)
	}
	return fmt.Sprintf("PR #%d to close", w.PR)
}

// GateDeadlineS is a gate's time box when none is given.
const GateDeadlineS = 24 * 3600

// OpenGate makes task tid, owned by Gate, watch w: it is queued by `by` and doing from now, and its deadline is its time box from now.
func OpenGate(q Q, tid int64, by string, w Watch) error {
	now := Now()
	_, err := q.Exec("UPDATE tasks SET watch_repo = ?, watch_pr = ?, watch_on = ?, queued_by = COALESCE(queued_by, ?), queued_at = COALESCE(queued_at, ?), "+
		"sent_at = ?, timeout_at = ? + COALESCE(timeout_s, ?) WHERE id = ?", w.Repo, w.PR, w.On, by, now, now, now, GateDeadlineS, tid)
	return err
}

// TaskWatch is what task tid watches, nil for a task that is no gate.
func TaskWatch(q Q, tid int64) (*Watch, error) {
	var repo, on, result sql.NullString
	var pr sql.NullInt64
	err := q.QueryRow("SELECT watch_repo, watch_pr, watch_on, watch_result FROM tasks WHERE id = ?", tid).Scan(&repo, &pr, &on, &result)
	if err == sql.ErrNoRows || (err == nil && !repo.Valid) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &Watch{Repo: repo.String, PR: pr.Int64, On: on.String, Result: result.String}, nil
}

// OpenGates are the gates still watching, oldest first, with their deadline (timeout_at, 0 for none).
func OpenGates(q Q) (ids []int64, watches []Watch, deadlines []int64, err error) {
	rows, err := q.Query("SELECT id, watch_repo, watch_pr, watch_on, COALESCE(timeout_at, 0) FROM tasks WHERE owner = ? AND watch_repo IS NOT NULL "+
		"AND done_evidence IS NULL AND discarded_by IS NULL ORDER BY id", Gate)
	if err != nil {
		return nil, nil, nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var id, deadline int64
		var w Watch
		if err := rows.Scan(&id, &w.Repo, &w.PR, &w.On, &deadline); err != nil {
			return nil, nil, nil, err
		}
		ids, watches, deadlines = append(ids, id), append(watches, w), append(deadlines, deadline)
	}
	return ids, watches, deadlines, rows.Err()
}

// SetWatchResult keeps what the gate opened with.
func SetWatchResult(q Q, tid int64, result string) error {
	_, err := q.Exec("UPDATE tasks SET watch_result = ? WHERE id = ?", result, tid)
	return err
}
