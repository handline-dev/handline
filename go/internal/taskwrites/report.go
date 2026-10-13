package taskwrites

import (
	"database/sql"
	"fmt"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/taskresult"
	"github.com/handline/handline/go/internal/textsafe"
)

// A done by an owner the task does not answer to is a report: the task waits, open, until whoever it answers to accepts it (it closes
// with the report as its evidence) or rejects it (it goes back to its owner's queue with a note).

// fileReport is MarkDone for an owner the task does not answer to, its checks passed: the report is kept, the owner's queued deliveries
// close and the owner is free for its next task, and whoever the task answers to is told how to accept or reject it.
func fileReport(tx *sql.Tx, by string, t *task, mark, via, from string, result taskresult.Result, finalize func() error) (*DoneResult, error) {
	res, err := tx.Exec("UPDATE tasks SET reported_at = ?, report = ?, report_via = ?, sent_at = NULL WHERE id = ? AND "+openSQL+" AND reported_at IS NULL",
		time.Now().Unix(), mark, via, t.ID)
	if err != nil {
		return nil, err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return nil, refusal(fmt.Sprintf("task T%d was reported done by someone else just now", t.ID))
	}
	if !result.Empty() {
		if err := taskresult.Save(tx, t.ID, result); err != nil {
			return nil, err
		}
	}
	_, forget, err := closeDeliveries(tx, t.ID)
	if err != nil {
		return nil, err
	}
	to := t.reporter()
	text := fmt.Sprintf("T%d reported done by %s: %s - accept it (`hl task accept T%d`) or send it back (`hl task reject T%d \"<note>\"`)",
		t.ID, by, strings.Join(strings.Fields(mark), " "), t.ID, t.ID)
	rid, err := addSay(tx, noticeFrom, to, text, false)
	if err != nil {
		return nil, err
	}
	if err := addEvent(tx, by, "task_report", kv{"task_id", t.ID}, kv{"report", mark}, kv{"from", from}, kv{"to", to}, kv{"request_id", rid}); err != nil {
		return nil, err
	}
	return &DoneResult{Said: fmt.Sprintf("task T%d reported done (%s); it closes when %s accepts it", t.ID, mark, to), Owner: t.Owner,
		Told: []told{{RID: rid, To: to, Text: text}}, Forget: forget, Reported: true, Keep: finalize}, nil
}

// reportFor reads task tid for an accept or a reject by `by`: refused unless a report waits on it and `by` is whoever it answers to, its
// opener or the founder.
func reportFor(tx *sql.Tx, tid int64, by, verb string) (*task, error) {
	t, err := getTask(tx, tid)
	if err != nil {
		return nil, err
	}
	if t == nil {
		return nil, refusal(fmt.Sprintf("no task T%d", tid))
	}
	if !t.reported() {
		return nil, refusal(fmt.Sprintf("task T%d has no report waiting - nothing to %s", tid, verb))
	}
	if by != t.reporter() && by != t.OpenedBy && by != human {
		return nil, refusal(fmt.Sprintf("task T%d's report can only be %sed by %s - whoever it answers to - or the founder", tid, verb, t.reporter()))
	}
	return t, nil
}

// acceptMethod is task.accept: the task closes done, with its report as the evidence, as a done by its owner would have.
func acceptMethod(home string, tell Teller) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		tid, err := taskID(p)
		if err != nil {
			return nil, err
		}
		by := c.Caller.Actor()
		var done *DoneResult
		out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			t, err := reportFor(tx, tid, by, "accept")
			if err != nil {
				return nil, err
			}
			if won, err := db.MarkTaskDone(tx, tid, t.Report.String, t.ReportVia.String); err != nil {
				return nil, err
			} else if !won {
				return nil, refusal(fmt.Sprintf("task T%d was closed by someone else just now", tid))
			}
			if err := addEvent(tx, by, "accept_task", kv{"task_id", tid}, kv{"done", t.Report.String}); err != nil {
				return nil, err
			}
			text := fmt.Sprintf("%s (accepted by %s)", doneText(tid, t.Owner, t.Report.String), by)
			notes, forget, err := endTask(tx, home, tid, by, t, text, urgent(t.Report.String), []string{t.Owner})
			if err != nil {
				return nil, err
			}
			done = &DoneResult{Owner: t.Owner, Told: notes, Forget: forget, Finished: true}
			return accepted(fmt.Sprintf("task T%d done (%s), accepted by %s", tid, t.Report.String, by), nil), nil
		})
		if err == nil && done != nil && tell != nil {
			tell.Done(tid, by, done.Owner, done.Told, done.Forget, true)
		}
		return out, err
	}
}

// rejectMethod is task.reject: the report is dropped and the task goes back to its owner's queue, the note told to the owner.
func rejectMethod(tell Teller) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		tid, err := taskID(p)
		if err != nil {
			return nil, err
		}
		note, _ := p["note"].(string)
		if note = strings.TrimSpace(textsafe.Oneline(note)); note == "" {
			return nil, badParam("note is the one line that says what is missing", "note")
		}
		by := c.Caller.Actor()
		var notes []told
		sent := false
		out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			t, err := reportFor(tx, tid, by, "reject")
			if err != nil {
				return nil, err
			}
			now := time.Now().Unix()
			if _, err := tx.Exec("UPDATE tasks SET reported_at = NULL, report = NULL, report_via = NULL, queued_by = COALESCE(queued_by, ?), "+
				"queued_at = COALESCE(queued_at, ?) WHERE id = ?", by, now, tid); err != nil {
				return nil, err
			}
			if err := taskresult.Save(tx, tid, taskresult.Result{}); err != nil { // the report's commit and shots go with it
				return nil, err
			}
			after, err := newestRequest(tx)
			if err != nil {
				return nil, err
			}
			if err := addEvent(tx, by, "reject_task", kv{"task_id", tid}, kv{"note", note}, kv{"after_request", after}); err != nil {
				return nil, err
			}
			if t.Owner != by {
				text := fmt.Sprintf("T%d sent back by %s: %s", tid, by, note)
				rid, err := addSay(tx, noticeFrom, t.Owner, text, false)
				if err != nil {
					return nil, err
				}
				notes = append(notes, told{RID: rid, To: t.Owner, Text: text})
			}
			sent = true
			return accepted(fmt.Sprintf("task T%d sent back to %s: %s", tid, t.Owner, note), nil), nil
		})
		if err == nil && sent && tell != nil {
			if said := tell.Rejected(tid, by, notes); said != "" {
				out["said"] = fmt.Sprintf("%v - %s", out["said"], said)
			}
		}
		return out, err
	}
}
