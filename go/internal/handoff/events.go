package handoff

import (
	"database/sql"
	"encoding/json"

	"github.com/handline/handline/go/internal/db"
)

// AtLimit is the limit this agent's last turn ended on, if nothing has run since, nil otherwise. It is the latest of StopFailure
// and UserPromptSubmit: a prompt after the failure means the agent is working again (a move's wake types one). The event's own
// fields are in the map, and its time as "at".
func AtLimit(q db.Q, agent string) (map[string]any, error) {
	var kind string
	var payload sql.NullString
	var ts int64
	err := q.QueryRow("SELECT kind, payload, ts FROM events WHERE agent = ? AND kind IN ('StopFailure', 'UserPromptSubmit') ORDER BY id DESC LIMIT 1",
		agent).Scan(&kind, &payload, &ts)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if kind != "StopFailure" {
		return nil, nil
	}
	var data map[string]any
	if !payload.Valid || json.Unmarshal([]byte(payload.String), &data) != nil || !truthy(data["limit"]) {
		return nil, nil
	}
	data["at"] = ts
	return data, nil
}

// RecordCommit is the git post-commit event: commit and diffstat, no model. The same commit is recorded once.
func RecordCommit(q db.Q, agent, repo, sha, subject, diffstat string) error {
	_, err := db.AddEventDeduped(q, agent, "commit", "commit:"+repo+":"+sha,
		db.KV{K: "repo", V: repo}, db.KV{K: "sha", V: sha}, db.KV{K: "subject", V: cutRunes(subject, 200)}, db.KV{K: "diffstat", V: cutRunes(diffstat, 300)})
	return err
}

// Failure is a turn that ended on a StopFailure: its event id and time, and the CLI's error field and details.
type Failure struct {
	ID, TS         int64
	Error, Details string
}

// sinceWorked is the newest event id on acct that says the account works or was cleared: a Stop on it, or account_unmarked.
// A failure at or before it says nothing about the account now.
func sinceWorked(q db.Q, acct string) (int64, error) {
	var id int64
	err := q.QueryRow("SELECT COALESCE(MAX(id), 0) FROM events WHERE account = ? AND kind IN ('Stop', 'account_unmarked')", acct).Scan(&id)
	return id, err
}

// LastFailure is the StopFailure this agent's last turn ended on, if nothing has run since, it happened on acct and the account has not
// worked or been cleared since: the CLI's own error field, never text. A failure on another account is not this one's (a move off it
// leaves that failure as the last event until the new session is prompted). ok is false otherwise.
func LastFailure(q db.Q, agent, acct string) (f Failure, ok bool, err error) {
	var kind string
	var payload, account sql.NullString
	err = q.QueryRow("SELECT id, ts, kind, payload, account FROM events WHERE agent = ? AND kind IN ('StopFailure', 'UserPromptSubmit') ORDER BY id DESC LIMIT 1",
		agent).Scan(&f.ID, &f.TS, &kind, &payload, &account)
	if err == sql.ErrNoRows {
		return Failure{}, false, nil
	}
	if err != nil || kind != "StopFailure" || account.String != acct {
		return Failure{}, false, err
	}
	var data map[string]any
	if !payload.Valid || json.Unmarshal([]byte(payload.String), &data) != nil {
		return Failure{}, false, nil
	}
	if f.Error, _ = data["error"].(string); f.Error == "" {
		return Failure{}, false, nil
	}
	f.Details, _ = data["error_details"].(string)
	if worked, err := sinceWorked(q, acct); err != nil || f.ID <= worked {
		return Failure{}, false, err
	}
	return f, true, nil
}

// Failures is the time of each StopFailure on errType on acct, by any agent, since the account last worked or was cleared.
func Failures(q db.Q, acct, errType string) ([]int64, error) {
	worked, err := sinceWorked(q, acct)
	if err != nil {
		return nil, err
	}
	rows, err := q.Query("SELECT ts FROM events WHERE kind = 'StopFailure' AND account = ? AND id > ? AND json_valid(payload) AND json_extract(payload, '$.error') = ?",
		acct, worked, errType)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []int64
	for rows.Next() {
		var ts int64
		if err := rows.Scan(&ts); err != nil {
			return nil, err
		}
		out = append(out, ts)
	}
	return out, rows.Err()
}
