package taskwrites

import (
	"database/sql"
	"fmt"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/rpc"
)

// mergeMethod is task.merge (actions.merge_tasks), anyone's call: each named task's title is added to the kept one's, its requests,
// sub-tasks, memos and the tasks waiting on it move to the kept task, and it closes as merged into it. All or none. The opener and assignee
// of every task in it are told.
func mergeMethod(home string, tell Teller) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		keep, err := taskID(map[string]any{"task": p["into"]})
		if err != nil {
			return nil, badParam("into is a task number", "into")
		}
		others, err := taskIDs(p, "tasks")
		if err != nil {
			return nil, err
		}
		by := c.Caller.Actor()
		var notes []told
		out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			notes = nil
			keepRow, rows, err := mergeChecks(tx, keep, others)
			if err != nil {
				return nil, err
			}
			var involved []*task
			for _, id := range append([]int64{keep}, others...) {
				t, err := getTask(tx, id)
				if err != nil {
					return nil, err
				}
				involved = append(involved, t)
			}
			title := keepRow.Title
			for _, r := range rows {
				res, err := tx.Exec("UPDATE tasks SET discarded_by = ?, discarded_at = ?, discard_reason = ?, merged_into = ? WHERE id = ? AND "+openSQL,
					by, time.Now().Unix(), fmt.Sprintf("merged into T%d", keep), keep, r.ID)
				if err != nil {
					return nil, err
				}
				if _, err := tx.Exec("UPDATE tasks SET sent_at = NULL WHERE id = ?", r.ID); err != nil {
					return nil, err
				}
				if n, _ := res.RowsAffected(); n == 0 {
					return nil, refusal(fmt.Sprintf("task T%d was done or discarded by someone else just now", r.ID))
				}
				edges, err := blocksOn(tx, keep) // keep waiting on the folded task is no wait on itself: that edge goes
				if err != nil {
					return nil, err
				}
				dropped := false
				for _, e := range edges {
					dropped = dropped || e == r.ID
				}
				if err := moveTaskLinks(tx, r.ID, keep); err != nil {
					return nil, err
				}
				payload := []kv{{"task_id", r.ID}, {"into", keep}}
				if dropped {
					payload = append(payload, kv{"dropped_wait", true})
				}
				if err := addEvent(tx, by, "merge_task", payload...); err != nil {
					return nil, err
				}
				title += fmt.Sprintf(" (+ T%d: %s)", r.ID, r.Title)
			}
			if _, err := tx.Exec("UPDATE tasks SET edited_by = ?, edited_at = ?, title = ? WHERE id = ?", by, time.Now().Unix(), title, keep); err != nil {
				return nil, err
			}
			names := make([]string, len(rows))
			for i, r := range rows {
				names[i] = fmt.Sprintf("T%d", r.ID)
			}
			said := fmt.Sprintf("%s merged into T%d by %s", strings.Join(names, ", "), keep, by)
			var who []string
			for _, t := range involved {
				who = append(who, t.OpenedBy, t.Owner)
			}
			if notes, err = tellTask(tx, home, involved[0], by, said, who...); err != nil {
				return nil, err
			}
			return accepted(said, nil), nil
		})
		if err == nil && tell != nil {
			tell.Notified(notes)
		}
		return out, err
	}
}

// mergeRow is the columns of a task the merge's checks and its title read.
type mergeRow struct {
	ID                        int64
	Title, OpenedBy, Reporter string
	Done, Discarded           bool
}

func mergeRowOf(tx *sql.Tx, tid int64) (*mergeRow, error) {
	r := &mergeRow{ID: tid}
	var done, disc, reporter sql.NullString
	err := tx.QueryRow("SELECT title, opened_by, done_evidence, discarded_by, reporter FROM tasks WHERE id = ?", tid).Scan(&r.Title, &r.OpenedBy, &done, &disc, &reporter)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	r.Done, r.Discarded = done.Valid && done.String != "", disc.Valid && disc.String != ""
	r.Reporter = r.OpenedBy
	if reporter.String != "" {
		r.Reporter = reporter.String
	}
	return r, err
}

// editGuard is _task_edit_guard, left only on release: whoever opened the task, its reporter or the founder.
func editGuard(r *mergeRow, by string) string {
	if by != r.OpenedBy && by != r.Reporter && by != human {
		return fmt.Sprintf("task T%d can only be edited by whoever opened it (%s or the founder", r.ID, openerAndReporter(r.OpenedBy, r.Reporter))
	}
	return ""
}

// openerAndReporter is _opener_and_reporter: the opener named in a refusal, then the reporter when it is someone else.
func openerAndReporter(opener, reporter string) string {
	if reporter == opener {
		return opener + ")"
	}
	return opener + "), its reporter (" + reporter + ")"
}

// guardRow is the part of a task editGuard reads.
func guardRow(t *task) *mergeRow {
	return &mergeRow{ID: t.ID, OpenedBy: t.OpenedBy, Reporter: t.reporter()}
}

// mergeChecks is _merge_checks: the refusal, if any, and the rows to fold, read in the merge's own transaction.
func mergeChecks(tx *sql.Tx, keep int64, others []int64) (*mergeRow, []*mergeRow, error) {
	keepRow, err := mergeRowOf(tx, keep)
	if err != nil {
		return nil, nil, err
	}
	if keepRow == nil {
		return nil, nil, refusal(fmt.Sprintf("no task T%d", keep))
	}
	seen := map[int64]bool{}
	for _, o := range others {
		if seen[o] {
			return nil, nil, refusal("a task is named twice")
		}
		seen[o] = true
	}
	if seen[keep] {
		return nil, nil, refusal(fmt.Sprintf("task T%d cannot merge into itself", keep))
	}
	if keepRow.Discarded {
		return nil, nil, refusal(fmt.Sprintf("task T%d is discarded - nothing can merge into it", keep))
	}
	if keepRow.Done {
		return nil, nil, refusal(fmt.Sprintf("task T%d is done - nothing can merge into it", keep))
	}
	var rows []*mergeRow
	for _, tid := range others {
		r, err := mergeRowOf(tx, tid)
		if err != nil {
			return nil, nil, err
		}
		if r == nil {
			return nil, nil, refusal(fmt.Sprintf("no task T%d", tid))
		}
		if why, err := mergeRefusal(tx, keepRow, r); err != nil || why != "" {
			if err == nil {
				err = refusal(why)
			}
			return nil, nil, err
		}
		rows = append(rows, r)
	}
	return keepRow, rows, nil
}

// mergeRefusal is _merge_refusal: why `r` cannot be folded into `keep`, or "".
func mergeRefusal(tx *sql.Tx, keep, r *mergeRow) (string, error) {
	if r.Discarded {
		return fmt.Sprintf("task T%d is already discarded", r.ID), nil
	}
	if r.Done {
		return fmt.Sprintf("task T%d is done - it is not merged", r.ID), nil
	}
	// A child's auto-block on its parent is no reason to refuse: moveTaskLinks reparents the child. Only a blocker that is not a child refuses.
	blockers, err := taskBlockers(tx, r.ID)
	if err != nil {
		return "", err
	}
	var waits []string
	for _, b := range blockers {
		if !b.Parent.Valid || b.Parent.Int64 != r.ID {
			waits = append(waits, fmt.Sprintf("T%d", b.ID))
		}
	}
	if len(waits) > 0 {
		return fmt.Sprintf("task T%d is waiting on %s - it cannot be merged until they are done", r.ID, strings.Join(waits, ", ")), nil
	}
	under, err := isUnder(tx, keep.ID, r.ID)
	if err != nil {
		return "", err
	}
	if under {
		return fmt.Sprintf("task T%d is under T%d - folding T%d into it would loop them", keep.ID, r.ID, r.ID), nil
	}
	deps, err := ids(tx, "SELECT task_id FROM task_blocks WHERE blocks_on = ? ORDER BY task_id", r.ID)
	if err != nil {
		return "", err
	}
	for _, dep := range deps {
		if dep == keep.ID {
			continue
		}
		cyc, err := blockedByCycle(tx, dep, keep.ID, map[int64]bool{})
		if err != nil {
			return "", err
		}
		if cyc {
			return fmt.Sprintf("T%d waits on T%d; waiting on T%d instead would be a cycle", dep, r.ID, keep.ID), nil
		}
	}
	return "", nil
}

// isUnder is _under: ancestor is tid itself or somewhere up its parent chain.
func isUnder(tx *sql.Tx, tid, ancestor int64) (bool, error) {
	seen := map[int64]bool{}
	for tid != 0 && !seen[tid] {
		if tid == ancestor {
			return true, nil
		}
		seen[tid] = true
		var parent sql.NullInt64
		err := tx.QueryRow("SELECT parent_task FROM tasks WHERE id = ?", tid).Scan(&parent)
		if err != nil && err != sql.ErrNoRows {
			return false, err
		}
		tid = parent.Int64 // NULL (or no row) ends the walk
	}
	return false, nil
}

// blocksOn is db.blocks_on_ids: every edge tid itself names, closed blockers too.
func blocksOn(tx *sql.Tx, tid int64) ([]int64, error) {
	return ids(tx, "SELECT blocks_on FROM task_blocks WHERE task_id = ?", tid)
}

// blockedByCycle is _blocked_by_cycle: tid waiting on `on` would close a loop.
func blockedByCycle(tx *sql.Tx, tid, on int64, seen map[int64]bool) (bool, error) {
	if on == tid {
		return true, nil
	}
	if seen[on] {
		return false, nil
	}
	seen[on] = true
	next, err := blocksOn(tx, on)
	if err != nil {
		return false, err
	}
	for _, n := range next {
		if cyc, err := blockedByCycle(tx, tid, n, seen); err != nil || cyc {
			return cyc, err
		}
	}
	return false, nil
}

// moveTaskLinks is db.move_task_links: tid's requests, sub-tasks, memos and the edges naming it go to `into`.
func moveTaskLinks(tx *sql.Tx, tid, into int64) error {
	for _, q := range []struct {
		sql  string
		args []any
	}{
		{"INSERT OR IGNORE INTO task_requests(task_id, request_id) SELECT ?, request_id FROM task_requests WHERE task_id = ?", []any{into, tid}},
		{"DELETE FROM task_requests WHERE task_id = ?", []any{tid}},
	} {
		if _, err := tx.Exec(q.sql, q.args...); err != nil {
			return err
		}
	}
	// tid is not refreshed: merge already unsent it and it has no request left. The kept task may now have one out.
	if err := refreshSent(tx, into); err != nil {
		return err
	}
	for _, q := range []struct {
		sql  string
		args []any
	}{
		{"UPDATE tasks SET parent_task = ? WHERE parent_task = ? AND id != ?", []any{into, tid, into}},
		{"UPDATE OR IGNORE task_blocks SET blocks_on = ? WHERE blocks_on = ? AND task_id != ?", []any{into, tid, into}},
		{"DELETE FROM task_blocks WHERE blocks_on = ?", []any{tid}},
		{"UPDATE OR IGNORE task_blocks SET task_id = ? WHERE task_id = ? AND blocks_on != ?", []any{into, tid, into}},
		{"DELETE FROM task_blocks WHERE task_id = ?", []any{tid}},
		{"UPDATE task_memos SET task_id = ? WHERE task_id = ?", []any{into, tid}},
	} {
		if _, err := tx.Exec(q.sql, q.args...); err != nil {
			return err
		}
	}
	return nil
}
