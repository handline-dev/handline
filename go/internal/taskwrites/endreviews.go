package taskwrites

import (
	"database/sql"
	"errors"
	"fmt"
	"os"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/team"
)

// EndReviews runs at daemon start: a task has no review state any more, so each one an older daemon left in review is moved, once, to a
// task that is queued or doing. Its answer is kept as a memo by "daemon". One not yet accepted is handed to whom it waited on (its owner's
// lead, else its reporter, else its opener); an accepted one stays with its owner, the memo naming the PR it waits on. It answers a line per
// task moved; a task that cannot be moved is left for the next start, and the others still move.
func EndReviews(d *sql.DB, home string, tell Teller) (said []string, err error) {
	type review struct {
		id                      int64
		owner, reporter, opener string
		report, accepted        string
		pr                      sql.NullInt64
	}
	rows, err := d.Query("SELECT id, owner, COALESCE(reporter, ''), COALESCE(opened_by, ''), COALESCE(report, ''), COALESCE(accepted_by, ''), pr FROM tasks " +
		"WHERE reported_at IS NOT NULL AND " + openSQL + " ORDER BY id")
	if err != nil {
		return nil, err
	}
	var all []review
	for rows.Next() {
		var r review
		if err := rows.Scan(&r.id, &r.owner, &r.reporter, &r.opener, &r.report, &r.accepted, &r.pr); err != nil {
			rows.Close()
			return nil, err
		}
		all = append(all, r)
	}
	rows.Close()
	if err := rows.Err(); err != nil || len(all) == 0 {
		return nil, err
	}
	tm, err := team.LoadTeam(home)
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	agents := paths.ListAgents(home)
	var failed error
	for _, r := range all {
		to := ""
		memo := fmt.Sprintf("Answered while tasks had a review state: %s", r.report)
		if r.accepted != "" {
			memo += fmt.Sprintf(" - accepted by %s", r.accepted)
			if r.pr.Valid {
				memo += fmt.Sprintf("; it waits on PR #%d", r.pr.Int64)
			}
		} else {
			for _, who := range []string{tm.LeadOf(r.owner), r.reporter, r.opener} {
				if who != "" {
					to = who
					break
				}
			}
		}
		if to == r.owner {
			to = ""
		}
		var h Handover
		var asked *Asked
		tx, err := d.Begin()
		if err != nil {
			return said, errors.Join(failed, err)
		}
		err = func() error {
			if _, err := tx.Exec("UPDATE tasks SET reported_at = NULL, report = NULL, report_via = NULL, accepted_by = NULL, accepted_at = NULL WHERE id = ?", r.id); err != nil {
				return err
			}
			if _, err := db.AddMemo(tx, r.id, "daemon", memo); err != nil {
				return err
			}
			if to == "" {
				_, err := db.SetTaskQueued(tx, r.id, "daemon")
				return err
			}
			var err error
			h, asked, err = handOff(tx, tell, r.id, "daemon", to, "for review: "+r.report, agents)
			if err == nil && !asked.OK {
				err = fmt.Errorf("handing T%d to %s was refused: %s", r.id, to, asked.Msg)
			}
			return err
		}()
		if err == nil {
			err = tx.Commit()
		}
		if err != nil {
			tx.Rollback()
			failed = errors.Join(failed, fmt.Errorf("T%d: %w", r.id, err))
			continue
		}
		if to == "" {
			said = append(said, fmt.Sprintf("T%d left review: queued with %s", r.id, r.owner))
			continue
		}
		if asked.After != nil {
			h.After = asked.After
		}
		tell.HandedOver(h)
		said = append(said, fmt.Sprintf("T%d left review: handed to %s", r.id, to))
	}
	return said, failed
}
