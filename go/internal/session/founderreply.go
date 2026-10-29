package session

import (
	"database/sql"
	"fmt"

	"github.com/handline/handline/go/internal/db"
)

// A founder's message is typed into the agent's terminal as a bare prompt, so an answer typed back in the terminal looks natural, and
// the founder never sees it. The Stop hook holds the agent once for each such message nothing answered.

// FounderNudgeKind is the event that marks a founder's message as nudged; its dedup key makes that once per message.
const FounderNudgeKind = "founder_nudged"

// FounderDeferKind marks a founder's message whose nudge a Stop paused for the session's own work put off, once per message: the
// message stays this turn's for the Stop after it, whatever that Stop's lists hold.
const FounderDeferKind = "founder_nudge_deferred"

// UnansweredFounder is the founder's typed message to agent that landed since agent's last recorded Stop or was deferred, was never nudged, and has no
// answer: no say or other request from agent to the founder since it landed, and no task or no-work mark made from it. 0 when none.
func (e *Env) UnansweredFounder(agent string) (int64, error) {
	var rid int64
	err := e.DB.QueryRow("SELECT r.id FROM requests r WHERE r.requester = ? AND r.target = ? AND r.kind = 'say' AND r.founder_auth = 1 "+
		"AND (r.landed_at >= (SELECT COALESCE(MAX(ts), 0) FROM events WHERE agent = ? AND kind IN ('Stop', 'StopFailure')) "+
		"OR EXISTS (SELECT 1 FROM events d WHERE d.dedup_key = ?||r.id)) "+
		"AND r.no_work IS NULL "+
		"AND NOT EXISTS (SELECT 1 FROM task_requests l WHERE l.request_id = r.id) "+
		"AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.from_request = r.id) "+
		"AND NOT EXISTS (SELECT 1 FROM requests a WHERE a.requester = r.target AND a.target = ? AND a.ts >= r.landed_at) "+
		"AND NOT EXISTS (SELECT 1 FROM events e WHERE e.dedup_key = ?||r.id) "+
		"ORDER BY r.id DESC LIMIT 1", db.Human, agent, agent, founderDeferKey, db.Human, founderNudgeKey).Scan(&rid)
	if err == sql.ErrNoRows {
		return 0, nil
	}
	return rid, err
}

const founderNudgeKey, founderDeferKey = "founder_nudge:", "founder_nudge_deferred:"

// NoteFounderNudged marks the founder's message rid as nudged; false when it already was.
func (e *Env) NoteFounderNudged(agent string, rid int64) (bool, error) {
	return e.noteFounder(agent, FounderNudgeKind, founderNudgeKey, rid)
}

// NoteFounderDeferred marks the founder's message rid as deferred; false when it already was.
func (e *Env) NoteFounderDeferred(agent string, rid int64) (bool, error) {
	return e.noteFounder(agent, FounderDeferKind, founderDeferKey, rid)
}

// noteFounder adds the kind event for rid once, by its key; false when it already was.
func (e *Env) noteFounder(agent, kind, key string, rid int64) (bool, error) {
	id, err := db.AddEventDeduped(e.DB, agent, kind, fmt.Sprintf("%s%d", key, rid), db.KV{K: "request_id", V: rid})
	return id != 0, err
}

// FounderNudgeText is the feedback the Stop hook gives for holding the agent.
func FounderNudgeText(name string) string {
	return fmt.Sprintf("The founder's message %s has no answer: your terminal text is not shown to them. "+
		"Answer it with hl say, then stop.", name)
}
