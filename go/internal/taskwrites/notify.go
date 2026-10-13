package taskwrites

import (
	"database/sql"

	"github.com/handline/handline/go/internal/paths"
)

// tellTask writes a notice of a change to task t for its opener, its assignee and anyone in also, to be delivered after the commit
// (Teller.Notified). Anyone may change a task, so these are who learn of it; the actor, the board and names no one holds are skipped.
func tellTask(tx *sql.Tx, home string, t *task, by, text string, also ...string) ([]told, error) {
	known := map[string]bool{human: true}
	for _, a := range paths.ListAgents(home) {
		known[a] = true
	}
	seen := map[string]bool{by: true, noticeFrom: true}
	var out []told
	for _, to := range append([]string{t.OpenedBy, t.Owner}, also...) {
		if seen[to] || !known[to] {
			continue
		}
		seen[to] = true
		rid, err := addSay(tx, noticeFrom, to, text, true)
		if err != nil {
			return nil, err
		}
		out = append(out, told{RID: rid, To: to, Text: text})
	}
	return out, nil
}
