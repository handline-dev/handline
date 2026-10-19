package main

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"regexp"
	"strconv"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/states"
)

// The Stop hook (session/stopgate.py): an agent is reminded, once per message, of a question put to it that it has not answered. A stop
// with no recorded wait is let through; the tick tells the agent's lead (tick.SweepStopWaitBackstop).

// atLimit is whether the agent's last turn ended on the usage limit with nothing run since (events.at_limit).
func atLimit(d *sql.DB, agent string) bool {
	var kind, payload string
	if d.QueryRow("SELECT kind, COALESCE(payload, '') FROM events WHERE agent = ? AND kind IN ('StopFailure', 'UserPromptSubmit') ORDER BY id DESC LIMIT 1",
		agent).Scan(&kind, &payload) != nil || kind != "StopFailure" {
		return false
	}
	var m map[string]any
	if json.Unmarshal([]byte(payload), &m) != nil {
		return false
	}
	l := m["limit"] // truthy, as Python read it
	return l != nil && l != false && l != "" && l != float64(0)
}

// atWall is whether typing at the agent's session would only spend quota: at its limit, or parked by a usage trigger (stopgate.wall_reason).
func atWall(d *sql.DB, agent string, row *db.AgentState) bool {
	if atLimit(d, agent) {
		return true
	}
	if row != nil && row.State == states.Parked && !(row.ClosedAt.Valid && row.ClosedAt.Int64 != 0) {
		kind, err := db.LastHandoffTrigger(d, agent)
		return err == nil && kind != "" && lifecycle.MovableTrigger(kind)
	}
	return false
}

var question = regexp.MustCompile(`[?？]`)

// unansweredQuestion is the latest message told to agent if it asks something and nothing has answered it, unless this gate reminded of it
// already: the founder's message asks, an agent's when it has a question mark; a reply, or the task board, asks nothing.
func unansweredQuestion(d *sql.DB, agent string) (int64, string, bool) {
	var rid int64
	var sender, body string
	var parent sql.NullInt64
	if d.QueryRow("SELECT id, requester, COALESCE(body, ''), parent_id FROM requests WHERE target = ? AND kind = 'say' AND requester != ? "+
		"AND delivered_at IS NOT NULL ORDER BY id DESC LIMIT 1", agent, db.NoticeRequester).Scan(&rid, &sender, &body, &parent) != nil {
		return 0, "", false
	}
	if parent.Valid || (sender != db.Human && !question.MatchString(body)) {
		return 0, "", false
	}
	if seen, _, _ := db.Meta(d, "answer_reminded:"+agent); seen == strconv.FormatInt(rid, 10) {
		return 0, "", false
	}
	var one int
	if d.QueryRow("SELECT 1 FROM requests WHERE requester = ? AND target = ? AND id > ? LIMIT 1", agent, sender, rid).Scan(&one) == nil {
		return 0, "", false
	}
	return rid, sender, true
}

// stopDecide is (block, reason) for a Stop event (stopgate.decide): the reminder of an unanswered question. Not for a repeat within one
// stop_hook_active chain, the founder, or an agent at the usage wall or whose session the daemon closed.
func stopDecide(d *sql.DB, agent string, raw map[string]any) (bool, string) {
	if agent == "" || agent == db.Human {
		return false, ""
	}
	if active, _ := raw["stop_hook_active"].(bool); active {
		return false, ""
	}
	row, err := db.GetAgentState(d, agent)
	if err != nil || atWall(d, agent, row) || (row != nil && row.ClosedAt.Valid && row.ClosedAt.Int64 != 0) {
		return false, ""
	}
	rid, sender, owed := unansweredQuestion(d, agent)
	if !owed {
		return false, ""
	}
	_ = db.SetMeta(d, "answer_reminded:"+agent, strconv.FormatInt(rid, 10))
	_, _ = db.AddEvent(d, agent, "stop_gate", db.KV{K: "answer_reminder", V: rid}, db.KV{K: "enforced", V: true})
	return true, fmt.Sprintf("%s may be waiting for an answer: if you have one, reply with `hl say %s --parent %s \"...\"` (what you write "+
		"in the terminal reaches no one).", db.MsgName(d, rid), sender, db.MsgName(d, rid))
}
