package taskwrites

import (
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
)

const founderBackKey = "founder_tasks_back"

// ReturnFounderTasks runs at daemon start, once: a task answers to the founder, it is not handed to them, so each open task an agent handed
// to them goes back to that agent (the last handoff to human, else its opener), with the note as a comment that names the founder, which asks
// them as any comment does. A task opened by the founder or the daemon (AskFounder, a notice), or whose agent is gone, stays with the founder.
// It answers a line per task moved; one that cannot be moved is left, and the step runs again at the next start until none is left.
func ReturnFounderTasks(d *sql.DB, home string, tell Teller) (said []string, err error) {
	if done, _, _ := db.Meta(d, founderBackKey); done == "done" {
		return nil, nil
	}
	rows, err := d.Query("SELECT t.id, t.opened_by, " +
		"COALESCE((SELECT e.payload FROM events e WHERE e.kind = 'task_handoff' AND json_valid(e.payload) AND json_extract(e.payload, '$.task_id') = t.id " +
		"AND json_extract(e.payload, '$.to') = 'human' AND json_extract(e.payload, '$.from') NOT IN ('human', 'gate', 'daemon') ORDER BY e.id DESC LIMIT 1), '') " +
		"FROM tasks t WHERE t.owner = 'human' AND t.done_evidence IS NULL AND t.discarded_by IS NULL")
	if err != nil {
		return nil, err
	}
	type back struct {
		id               int64
		to, note, opener string
	}
	var all []back
	for rows.Next() {
		var b back
		var payload string
		if err := rows.Scan(&b.id, &b.opener, &payload); err != nil {
			rows.Close()
			return nil, err
		}
		b.to = b.opener
		if payload != "" {
			var p struct {
				From string `json:"from"`
				Note string `json:"note"`
			}
			if err := json.Unmarshal([]byte(payload), &p); err == nil && p.From != "" {
				b.to, b.note = p.From, p.Note
			}
		}
		all = append(all, b)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return nil, err
	}
	agents := paths.ListAgents(home)
	var failed error
	left := false
	for _, b := range all {
		if !contains(agents, b.to) {
			continue // a daemon notice or a retired agent's: it stays the founder's
		}
		var h Handover
		var asked *Asked
		tx, err := d.Begin()
		if err != nil {
			return said, err
		}
		err = func() error {
			var err error
			h, asked, err = handOff(tx, tell, b.id, "daemon", b.to, "Ask the founder with a comment naming them; this task stays with you.", agents)
			if err == nil && !asked.OK {
				err = fmt.Errorf("handing it to %s was refused: %s", b.to, asked.Msg)
			}
			if err != nil {
				return err
			}
			body := strings.TrimSpace("@human " + b.note)
			mid, err := db.AddMemo(tx, b.id, b.to, body)
			if err != nil {
				return err
			}
			return syncMemoAsk(tx, b.id, mid, b.to, body)
		}()
		if err == nil {
			err = tx.Commit()
		}
		if err != nil {
			tx.Rollback()
			failed = errors.Join(failed, fmt.Errorf("T%d: %w", b.id, err))
			left = true
			continue
		}
		if asked.After != nil {
			h.After = asked.After
		}
		tell.HandedOver(h)
		said = append(said, fmt.Sprintf("T%d goes back to %s", b.id, b.to))
	}
	if !left {
		err = db.SetMeta(d, founderBackKey, "done")
	}
	return said, errors.Join(failed, err)
}
