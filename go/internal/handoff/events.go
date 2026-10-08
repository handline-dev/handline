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
	if err == sql.ErrNoRows || kind != "StopFailure" {
		return nil, nil
	}
	if err != nil {
		return nil, err
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
