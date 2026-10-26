package housekeeping

import (
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
)

const (
	// ChaseAfterS is how long a founder's message to a lead may stand with neither a task nor a no-work mark before the lead is told.
	ChaseAfterS = 30 * 60
	// chaseWindowS is how far back a message is looked at, so the first pass after the sweep lands does not chase old history.
	chaseWindowS = 24 * 60 * 60
	chaseKind    = "request_chased"
)

// Unanswered tells a lead, once, of a message of the founder's that began a thread and has neither a task made from it nor a no-work mark
// after ChaseAfterS. Replies inside a thread are not chased.
type Unanswered struct {
	// Lead is whether the agent answers to no one: only a lead is chased.
	Lead func(agent string) bool
	// Notice writes one notice from the task board to an agent.
	Notice func(q db.Q, to, text, reason string) error
	Log    func(string)
}

// Check runs on the housekeeping pass.
func (u *Unanswered) Check(q db.Q, now int64) error {
	rows, err := q.Query("SELECT r.id, r.target, r.body FROM requests r WHERE r.requester = ? AND r.target != ? AND r.parent_id IS NULL "+
		"AND r.state IN ('landed', 'queued') AND r.ts <= ? AND r.ts > ? AND r.no_work IS NULL "+
		"AND NOT EXISTS (SELECT 1 FROM task_requests l WHERE l.request_id = r.id) "+
		"AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.from_request = r.id) "+
		"AND NOT EXISTS (SELECT 1 FROM events e WHERE e.kind = ? AND CASE WHEN json_valid(e.payload) THEN json_extract(e.payload, '$.request_id') END = r.id) "+
		"ORDER BY r.id", db.Human, db.Human, now-ChaseAfterS, now-chaseWindowS, chaseKind)
	if err != nil {
		return err
	}
	type chase struct {
		id           int64
		target, body string
	}
	var due []chase
	for rows.Next() {
		var c chase
		if err := rows.Scan(&c.id, &c.target, &c.body); err != nil {
			rows.Close()
			return err
		}
		due = append(due, c)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return err
	}
	for _, c := range due {
		if !u.Lead(c.target) {
			continue
		}
		name := db.MsgName(q, c.id)
		first := strings.TrimSpace(strings.SplitN(strings.TrimSpace(c.body), "\n", 2)[0])
		if r := []rune(first); len(r) > 80 {
			first = string(r[:80]) + "…"
		}
		text := fmt.Sprintf("The founder's message %s (%q) has no task after 30 minutes: `hl task request %s` makes it a request, or "+
			"`hl task none %s \"why\"` says it needs no work.", name, first, name, name)
		if err := u.Notice(q, c.target, text, "request_chased"); err != nil {
			return err
		}
		if _, err := db.AddEvent(q, db.NoticeRequester, chaseKind, db.KV{K: "request_id", V: c.id}, db.KV{K: "to", V: c.target}); err != nil {
			return err
		}
		u.Log(fmt.Sprintf("chased %s for %s", name, c.target))
	}
	return nil
}
