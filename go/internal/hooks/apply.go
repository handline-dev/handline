// Package hooks is the record path: what a session's hook tells the daemon (hook.record), the spool a hook leaves
// its event in when the daemon is down, and what the daemon writes down for each event. It follows
// handline/session/events.py (apply_hook) and handline/daemon/spool.py, over the same tables and files.
package hooks

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/handline/handline/go/internal/rpc"
)

const hookKeep = 24 * time.Hour

// captured are the hook events that are recorded; PostToolUse is deliberately absent.
var captured = set("SessionStart", "SessionEnd", "Stop", "StopFailure", "SubagentStop", "PostCompact", "UserPromptSubmit",
	"PermissionRequest", "TeammateIdle")

// answers say a permission prompt was answered or a tool ran: they are never recorded.
var answers = set("PostToolUse", "PostToolUseFailure")

// endsWait are the events after which no prompt can still be pending.
var endsWait = set("UserPromptSubmit", "Stop", "StopFailure", "SessionStart", "SessionEnd")

// allowlist is, per event, the stdin fields worth keeping.
var allowlist = []string{"session_id", "hook_event_name", "source", "reason", "cwd", "prompt_length", "transcript_path",
	"model", "trigger", "tool_name", "error", "error_details"}

func set(names ...string) map[string]bool {
	m := make(map[string]bool, len(names))
	for _, n := range names {
		m[n] = true
	}
	return m
}

func cut(s string, n int) string {
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	return string([]rune(s)[:n])
}

// slim is the allowlisted fields of what the hook was told. A prompt's text is never stored, its length is.
func slim(raw map[string]any) map[string]any {
	out := map[string]any{}
	for _, k := range allowlist {
		v := raw[k]
		if v == nil {
			continue
		}
		switch t := v.(type) {
		case string:
			switch k {
			case "transcript_path":
				out[k] = t
			case "error_details":
				out[k] = cut(t, 200)
			default:
				out[k] = cut(t, 400)
			}
		case json.Number, bool:
			if k == "transcript_path" {
				out[k] = fmt.Sprint(t)
			} else {
				out[k] = t
			}
		case map[string]any:
			if k == "model" {
				out[k] = t["id"]
			}
		default:
			if k == "transcript_path" {
				out[k] = fmt.Sprint(t)
			}
		}
	}
	if p, ok := raw["prompt"].(string); ok {
		out["prompt_length"] = int64(utf8.RuneCountInString(p))
	}
	return out
}

// limitReason is the usage limit a StopFailure ended on, read from its error field only, never from text.
func limitReason(raw map[string]any) (string, bool) {
	if raw["error"] != "rate_limit" {
		return "", false
	}
	text := "rate_limit"
	if d, ok := raw["error_details"].(string); ok && d != "" {
		text = d
	}
	return cut(text, 200), true
}

// Apply is what the daemon does with a hook event: the one writer of what a hook records. Once per request id:
// the id is taken first, in the same transaction as every effect, so a line already applied does nothing at all.
//
// An event older than the newest already applied for this agent is recorded but moves nothing. A time ahead of
// the daemon's clock is taken as the time of arrival. On equal times the one that arrives later wins.
func Apply(db *sql.DB, home, agent, event string, raw map[string]any, requestID string, at int64) error {
	tx, err := db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	now := time.Now().Unix()
	if at > now {
		at = now // an event cannot have happened after it arrived
	}
	if !spoolPending(home) { // a file left by a crash may still replay lines older than a day
		// Ids older than a day go, except each agent's newest row: the lateness check below reads it.
		if _, err := tx.Exec("DELETE FROM hook_applied WHERE ts < ? AND rowid NOT IN "+
			"(SELECT rowid FROM hook_applied WHERE agent IS NOT NULL AND at IS NOT NULL GROUP BY agent HAVING at = MAX(at))",
			now-int64(hookKeep.Seconds())); err != nil {
			return err
		}
	}
	var newest sql.NullInt64
	if err := tx.QueryRow("SELECT MAX(at) FROM hook_applied WHERE agent = ?", agent).Scan(&newest); err != nil {
		return err
	}
	res, err := tx.Exec("INSERT OR IGNORE INTO hook_applied(request_id, ts, agent, at) VALUES(?, ?, ?, ?)", requestID, now, agent, at)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return nil
	}
	if err := apply(tx, home, agent, event, raw, at, newest.Valid && at < newest.Int64); err != nil {
		return err
	}
	return tx.Commit()
}

func apply(tx *sql.Tx, home, agent, event string, raw map[string]any, at int64, late bool) error {
	if answers[event] && !late {
		if err := noteToolUse(tx, agent); err != nil {
			return err
		}
	}
	if !captured[event] {
		return nil
	}
	var account sql.NullString
	if err := tx.QueryRow("SELECT account FROM agent_state WHERE agent = ?", agent).Scan(&account); err != nil && err != sql.ErrNoRows {
		return err
	}
	payload := slim(raw)
	if event == "StopFailure" {
		if text, ok := limitReason(raw); ok {
			payload["limit"], payload["limit_source"] = text, "payload.error"
		}
	}
	if event == "PermissionRequest" {
		payload["command"] = summary(raw["tool_input"])
		// Observation only: what a permission hook is told. Key names, no values.
		if m, ok := raw["permission_mode"].(string); ok {
			payload["permission_mode"] = cut(m, 40)
		}
		keys := make([]string, 0, len(raw))
		for k := range raw {
			keys = append(keys, cut(k, 40))
		}
		sort.Strings(keys)
		if len(keys) > 40 {
			keys = keys[:40]
		}
		payload["input_keys"] = keys
	} else if endsWait[event] && !late {
		clearWait(home, agent)
	}
	var body any
	if len(payload) > 0 {
		body = rpc.PyJSON(payload, false, false)
	}
	var acct any
	if account.Valid && account.String != "" {
		acct = account.String
	}
	res, err := tx.Exec("INSERT INTO events(ts, agent, account, kind, payload, dedup_key) VALUES(?,?,?,?,?,NULL)",
		at, agent, acct, event, body)
	if err != nil {
		return err
	}
	if late {
		return nil // recorded, and nothing it would change is changed: a newer event has been applied
	}
	eid, _ := res.LastInsertId()
	if sid, ok := raw["session_id"].(string); ok && sid != "" && event == "SessionStart" && raw["source"] == "startup" {
		if err := bindSessionStart(tx, agent, sid); err != nil {
			return err
		}
	}
	if event == "PermissionRequest" {
		// After its own row exists, so the id later compared against is this exact prompt's.
		tool, _ := raw["tool_name"].(string)
		noteWait(home, agent, tool, raw["tool_input"], eid)
	}
	return markActivity(tx, agent, event, payload)
}

const bindWindow = 600 * time.Second

// bindSessionStart: a fresh session's SessionStart names it: the newest fresh start of this agent's that no session
// has claimed, if one was written just now, is the one it came from.
func bindSessionStart(tx *sql.Tx, agent, session string) error {
	var id int64
	err := tx.QueryRow("SELECT id FROM session_starts WHERE agent = ? AND session_id IS NULL AND resumed = 0 AND ts >= ? "+
		"ORDER BY id DESC LIMIT 1", agent, time.Now().Add(-bindWindow).Unix()).Scan(&id)
	if err == sql.ErrNoRows {
		return nil
	}
	if err != nil {
		return err
	}
	_, err = tx.Exec("UPDATE session_starts SET session_id = ? WHERE id = ?", session, id)
	return err
}

// The daemon owns these states; an agent must not move itself out of them.
var protected = set("STALLED", "HANDOFF")

type stateRow struct {
	state, detail string
	wakeAt        int64
	closedAt      int64
	found         bool
}

func getState(tx *sql.Tx, agent string) (stateRow, error) {
	var r stateRow
	var detail sql.NullString
	var wake, closed sql.NullInt64
	err := tx.QueryRow("SELECT state, detail, wake_at, closed_at FROM agent_state WHERE agent = ?", agent).Scan(&r.state, &detail, &wake, &closed)
	if err == sql.ErrNoRows {
		return r, nil
	}
	r.found, r.detail, r.wakeAt, r.closedAt = err == nil, detail.String, wake.Int64, closed.Int64
	return r, err
}

func setState(tx *sql.Tx, agent, state, detail string) error {
	_, err := tx.Exec("INSERT INTO agent_state(agent, state, wake_at, detail, updated) VALUES(?,?,NULL,?,?) "+
		"ON CONFLICT(agent) DO UPDATE SET state=excluded.state, wake_at=excluded.wake_at, detail=excluded.detail, updated=excluded.updated",
		agent, state, detail, time.Now().Unix())
	return err
}

// markActivity lets the daemon know whether the agent is working: only a prompt does, and a turn's end says it stopped.
func markActivity(tx *sql.Tx, agent, event string, payload map[string]any) error {
	row, err := getState(tx, agent)
	if err != nil || (row.found && protected[row.state]) {
		return err
	}
	switch {
	case event == "UserPromptSubmit":
		if row.found && row.state == "RUNNING" && row.wakeAt == 0 {
			return nil // already correct; do not rewrite on every prompt
		}
		return setState(tx, agent, "RUNNING", "active ("+event+")")
	case event == "StopFailure" && payload["limit"] != nil && payload["limit"] != "":
		// Out of usage is not working: the turn ended and nothing will run.
		if row.found && row.state == "RUNNING" {
			return setState(tx, agent, "IDLE", "at its usage limit: "+cut(fmt.Sprint(payload["limit"]), 80))
		}
	case event == "Stop" && row.found && row.state == "RUNNING":
		return setState(tx, agent, "IDLE", "finished its turn")
	}
	return nil
}

// noteToolUse: a tool just ran, so the agent is mid-turn. Only a row the daemon is not steering (QUEUED not
// moving, IDLE, session not closed) is moved; the turn's Stop sets IDLE again.
func noteToolUse(tx *sql.Tx, agent string) error {
	row, err := getState(tx, agent)
	if err != nil || !row.found || row.closedAt != 0 || (row.state != "QUEUED" && row.state != "IDLE") ||
		strings.HasPrefix(row.detail, "moving") {
		return err
	}
	return setState(tx, agent, "RUNNING", "active (tool use)")
}
