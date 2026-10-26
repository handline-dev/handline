package supervisor

import (
	"database/sql"
	"fmt"

	"github.com/handline/handline/go/internal/db"
)

// SaidStopText is the notice for an agent that wrote to the founder and stopped with tid in doing: a say is not a question the board
// knows about, a task handed to the founder is.
func SaidStopText(tid int64) string {
	return fmt.Sprintf("You wrote to the founder and stopped with T%d in doing. If you wait for an answer, hand it: "+
		"hl task handoff T%d human \"<question>\". Otherwise go on.", tid, tid)
}

// SweepSaidAndStopped tells, once per turn and task, each agent whose last turn ended (Stop) after it wrote a say to the founder and
// that still holds its doing task. Nothing reads the say's text.
func (e *Env) SweepSaidAndStopped() (err error) {
	defer Catch(&err)
	q := db.Q(e.DB)
	type held struct {
		agent string
		tid   int64
	}
	var all []held
	rows, err := q.Query("SELECT agent, current_task FROM agent_state WHERE current_task IS NOT NULL AND agent != ?", db.Human)
	check(err)
	for rows.Next() {
		var h held
		check(rows.Scan(&h.agent, &h.tid))
		all = append(all, h)
	}
	check(rows.Err())
	rows.Close()
	for _, h := range all {
		stop, ok := e.saidThenStopped(q, h.agent)
		if !ok {
			continue
		}
		t := must(db.GetTask(q, h.tid))
		if t == nil || t.Owner != h.agent || !must(db.IsDoing(q, h.tid)) {
			continue
		}
		if must(db.AddEventDeduped(q, h.agent, "said_stop_told", fmt.Sprintf("said_stop:%s:%d:%d", h.agent, stop, h.tid),
			db.KV{K: "task_id", V: h.tid})) == 0 {
			continue
		}
		check(e.SystemSay(db.NoticeRequester, h.agent, SaidStopText(h.tid)))
		e.log("  %s: wrote to the founder and stopped with T%d in doing - told", h.agent, h.tid)
	}
	return nil
}

// saidThenStopped is the id of agent's Stop when its last turn ended with one and wrote a say to the founder.
func (e *Env) saidThenStopped(q db.Q, agent string) (int64, bool) {
	var id, at int64
	var kind string
	err := q.QueryRow("SELECT id, kind, ts FROM events WHERE agent = ? AND kind IN ('UserPromptSubmit', 'Stop', 'StopFailure') ORDER BY id DESC LIMIT 1",
		agent).Scan(&id, &kind, &at)
	if err == sql.ErrNoRows {
		return 0, false
	}
	check(err)
	if kind != "Stop" {
		return 0, false
	}
	var from int64
	check(q.QueryRow("SELECT COALESCE(MAX(ts), 0) FROM events WHERE agent = ? AND kind = 'UserPromptSubmit' AND id < ?", agent, id).Scan(&from))
	var said bool
	check(q.QueryRow("SELECT EXISTS(SELECT 1 FROM requests WHERE requester = ? AND target = ? AND kind = 'say' AND ts BETWEEN ? AND ?)",
		agent, db.Human, from, at).Scan(&said))
	return id, said
}
