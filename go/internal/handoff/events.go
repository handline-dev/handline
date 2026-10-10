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

// AuthFailed is the id, time and details of the authentication_failed turn this agent's last turn ended on, if nothing has run since and it
// happened on acct: the CLI's own word that its login was refused, read from the StopFailure's error field, never from text. A refusal
// on another account is not this one's (a move off it leaves that failure as the last event until the new session is prompted).
// ok is false otherwise.
func AuthFailed(q db.Q, agent, acct string) (id, ts int64, details string, ok bool, err error) {
	var kind string
	var payload, account sql.NullString
	err = q.QueryRow("SELECT id, ts, kind, payload, account FROM events WHERE agent = ? AND kind IN ('StopFailure', 'UserPromptSubmit') ORDER BY id DESC LIMIT 1",
		agent).Scan(&id, &ts, &kind, &payload, &account)
	if err == sql.ErrNoRows {
		return 0, 0, "", false, nil
	}
	if err != nil || kind != "StopFailure" || account.String != acct {
		return 0, 0, "", false, err
	}
	var data map[string]any
	if !payload.Valid || json.Unmarshal([]byte(payload.String), &data) != nil || data["error"] != "authentication_failed" {
		return 0, 0, "", false, nil
	}
	details, _ = data["error_details"].(string)
	return id, ts, details, true, nil
}
