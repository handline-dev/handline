package taskwrites

import (
	"database/sql"

	"github.com/handline/handline/go/internal/paths"
)

// tellTask writes a notice of a change to a task for each of to (its opener and assignee, and whoever else the change concerns), to be
// delivered after the commit (Teller.Notified). Anyone may change a task, so these are who learn of it; the actor, the board and names no
// one holds are skipped.
func tellTask(tx *sql.Tx, home, by, text string, to ...string) ([]told, error) {
	known := map[string]bool{human: true}
	for _, a := range paths.ListAgents(home) {
		known[a] = true
	}
	seen := map[string]bool{by: true, noticeFrom: true}
	var out []told
	for _, who := range to {
		if seen[who] || !known[who] {
			continue
		}
		seen[who] = true
		rid, err := addSay(tx, noticeFrom, who, text, true)
		if err != nil {
			return nil, err
		}
		out = append(out, told{RID: rid, To: who, Text: text})
	}
	return out, nil
}
