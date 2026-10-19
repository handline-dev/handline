package taskwrites

import (
	"database/sql"
	"errors"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/attachments"
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/delivery"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/taskresult"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/textsafe"
)

// A done by an assignee whose task does not close at once is an answer in review: the task stays with its assignee until its lead, its
// team's auditor, whoever it answers to or the founder accepts it (it closes with the answer as its evidence), rejects it (it goes back to
// its assignee's queue with a note) or takes it back. An accepted task with a PR waits on, and closes at, that PR's merge.

// reviewer is who a done by `by` on t waits on, "" when it closes at once: a done by its reporter or opener, by its team's auditor (that
// done is the review), by an assignee with no lead, or the founder's close. The founder's done on a task they hold that someone else asked
// waits on whoever asked it.
func reviewer(home, by string, t *task, close bool) (string, error) {
	switch {
	case by == t.reporter() || by == t.OpenedBy:
		return "", nil
	case by == human:
		if t.Owner == human && !t.reported() && !close {
			return t.reporter(), nil
		}
		return "", nil
	}
	tm, err := loadTeam(home)
	if err != nil || tm.AuditorOf(by) == by {
		return "", err
	}
	return tm.LeadOf(t.Owner), nil
}

// mayReview is whether `by` may accept or reject t's answer: its reporter, its opener, the founder, or its assignee's lead or auditor.
func mayReview(home, by string, t *task) (bool, error) {
	if by == human || by == t.reporter() || by == t.OpenedBy {
		return true, nil
	}
	tm, err := loadTeam(home)
	if err != nil {
		return false, err
	}
	return by == tm.LeadOf(t.Owner) || by == tm.AuditorOf(t.Owner), nil
}

// loadTeam is team.yaml, nil (no leads, no auditors) when there is none.
func loadTeam(home string) (*team.Team, error) {
	tm, err := team.LoadTeam(home)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	return tm, err
}

// fileReport is MarkDone for a done that goes to review, its checks passed: the answer is kept, the assignee's queued deliveries close and
// it is free for its next task, and `to`, who the review waits on, is told how to accept, reject or take it back.
func fileReport(tx *sql.Tx, by, to string, t *task, mark, via, from string, result taskresult.Result, finalize func() error) (*DoneResult, error) {
	res, err := tx.Exec("UPDATE tasks SET reported_at = ?, report = ?, report_via = ?, sent_at = NULL WHERE id = ? AND "+openSQL+" AND reported_at IS NULL",
		time.Now().Unix(), mark, via, t.ID)
	if err != nil {
		return nil, err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return nil, refusal(fmt.Sprintf("task T%d was answered by someone else just now", t.ID))
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
	// The delivery it landed on no longer holds its queue, or every later request would wait for the accept.
	if err := db.EndHeld(tx, t.ID, by); err != nil {
		return nil, err
	}
	who := by
	if by == human {
		who = "the founder"
	}
	text := fmt.Sprintf("T%d answered by %s: %s - accept it (`hl task accept T%d`), send it back (`hl task reject T%d \"<note>\"`) "+
		"or take it back (`hl task handoff T%d %s \"<note>\"`)", t.ID, who, strings.Join(strings.Fields(mark), " "), t.ID, t.ID, t.ID, to)
	rid, err := addSay(tx, noticeFrom, to, text, false)
	if err != nil {
		return nil, err
	}
	if err := addEvent(tx, by, "task_report", kv{"task_id", t.ID}, kv{"report", mark}, kv{"from", from}, kv{"to", to}, kv{"request_id", rid}); err != nil {
		return nil, err
	}
	return &DoneResult{Said: fmt.Sprintf("task T%d answered (%s); it stays with %s until %s accepts it or takes it back", t.ID, mark, t.Owner, to), Owner: t.Owner,
		Told: []told{{RID: rid, To: to, Text: text}}, Forget: forget, Reported: true, Keep: finalize}, nil
}

// reportFor reads task tid for an accept or a reject by `by`: refused unless an answer waits on it and `by` may review it (mayReview).
// proof, when not 0, is the founder's chat message that approved it (founderProof), checked and kept with the accept.
func reportFor(tx *sql.Tx, home string, tid int64, by, verb string, proof int64) (*task, error) {
	t, err := getTask(tx, tid)
	if err != nil {
		return nil, err
	}
	if t == nil {
		return nil, refusal(fmt.Sprintf("no task T%d", tid))
	}
	if !t.reported() {
		return nil, refusal(fmt.Sprintf("task T%d has no answer waiting - nothing to %s", tid, verb))
	}
	if verb == "accept" && t.AcceptedBy.String != "" {
		return nil, refusal(fmt.Sprintf("task T%d is already accepted by %s - it closes when PR #%d merges", tid, t.AcceptedBy.String, t.PR.Int64))
	}
	if proof != 0 {
		if err := founderProof(tx, t, proof); err != nil {
			return nil, err
		}
	}
	if ok, err := mayReview(home, by, t); err != nil || !ok {
		if err == nil {
			err = refusal(fmt.Sprintf("task T%d's answer can only be %sed by its assignee's lead or auditor, its reporter (%s) or the founder", tid, verb, t.reporter()))
		}
		return nil, err
	}
	return t, nil
}

// founderProof is the founder's chat message mid kept as the proof of an accept: refused unless the founder wrote it, to this task's
// report or later, tied to the task itself or as a reply to a message that is.
func founderProof(tx *sql.Tx, t *task, mid int64) error {
	var from string
	var at int64
	err := tx.QueryRow("SELECT r.requester, r.ts FROM requests r WHERE r.id = ? AND EXISTS (SELECT 1 FROM task_requests l "+
		"WHERE l.task_id = ? AND l.request_id IN (r.id, r.parent_id))", mid, t.ID).Scan(&from, &at)
	if err == sql.ErrNoRows {
		return refusal(fmt.Sprintf("%s is not tied to task T%d", db.MsgName(tx, mid), t.ID))
	}
	if err != nil {
		return err
	}
	if from != human {
		return refusal(fmt.Sprintf("%s is from %s, not the founder", db.MsgName(tx, mid), from))
	}
	if at < t.ReportedAt.Int64 {
		return refusal(fmt.Sprintf("%s was sent before T%d was answered", db.MsgName(tx, mid), t.ID))
	}
	return nil
}

// acceptMethod is task.accept: the task closes done, with its report as the evidence, as a done by its owner would have. by_founder names
// the founder's chat message that approved it, kept in the accept_task event.
func acceptMethod(home string, tell Teller) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		tid, err := taskID(p)
		if err != nil {
			return nil, err
		}
		by := c.Caller.Actor()
		var done *DoneResult
		var merge []told
		out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			merge = nil
			mid, byFounder := rpc.AsInt(p["by_founder"])
			if p["by_founder"] != nil && (!byFounder || mid <= 0) {
				return nil, badParam("by_founder is a message id", "by_founder")
			}
			t, err := reportFor(tx, home, tid, by, "accept", mid)
			if err != nil {
				return nil, err
			}
			if t.PR.Valid {
				var out map[string]any
				out, merge, err = acceptForMerge(tx, by, t, mid)
				return out, err
			}
			if won, err := db.MarkTaskDone(tx, tid, t.Report.String, t.ReportVia.String); err != nil {
				return nil, err
			} else if !won {
				return nil, refusal(fmt.Sprintf("task T%d was closed by someone else just now", tid))
			}
			ev := []kv{{"task_id", tid}, {"done", t.Report.String}}
			if byFounder {
				ev = append(ev, kv{"by_founder", mid})
			}
			if err := addEvent(tx, by, "accept_task", ev...); err != nil {
				return nil, err
			}
			text := fmt.Sprintf("%s (accepted by %s)", doneText(tid, t.Owner, t.Report.String), by)
			notes, forget, err := endTask(tx, home, tid, by, t, text, delivery.Failed(t.Report.String), []string{t.Owner})
			if err != nil {
				return nil, err
			}
			done = &DoneResult{Owner: t.Owner, Told: notes, Forget: forget, Finished: true}
			return accepted(fmt.Sprintf("task T%d done (%s), accepted by %s", tid, t.Report.String, by), nil), nil
		})
		if err == nil && done != nil && tell != nil {
			tell.Done(tid, by, done.Owner, done.Told, done.Forget, true)
		}
		if err == nil && len(merge) > 0 && tell != nil {
			tell.Notified(merge)
		}
		return out, err
	}
}

// acceptForMerge is task.accept on a task with a PR: the task stays in review, accepted, until the PR merges; its assignee is told.
func acceptForMerge(tx *sql.Tx, by string, t *task, proof int64) (map[string]any, []told, error) {
	res, err := tx.Exec("UPDATE tasks SET accepted_by = ?, accepted_at = ? WHERE id = ? AND accepted_by IS NULL AND reported_at IS NOT NULL AND "+openSQL,
		by, time.Now().Unix(), t.ID)
	if err != nil {
		return nil, nil, err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return nil, nil, refusal(fmt.Sprintf("task T%d was accepted or closed by someone else just now", t.ID))
	}
	ev := []kv{{"task_id", t.ID}, {"done", t.Report.String}, {"pr", t.PR.Int64}}
	if proof != 0 {
		ev = append(ev, kv{"by_founder", proof})
	}
	if err := addEvent(tx, by, "accept_task", ev...); err != nil {
		return nil, nil, err
	}
	said := fmt.Sprintf("task T%d accepted by %s; it closes when PR #%d merges", t.ID, by, t.PR.Int64)
	var notes []told
	if t.Owner != by {
		rid, err := addSay(tx, noticeFrom, t.Owner, said, false)
		if err != nil {
			return nil, nil, err
		}
		notes = append(notes, told{RID: rid, To: t.Owner, Text: said})
	}
	return accepted(said, nil), notes, nil
}

// rejectNote is task.reject's note as kept: one line of words, then each image it names on a line of its own.
func rejectNote(note string) (string, error) {
	refs := attachments.Refs(note)
	words := note
	if len(refs) > 0 {
		words = note[:refs[0].Start]
	}
	lines := []string{strings.TrimSpace(textsafe.Oneline(words))}
	if lines[0] == "" {
		return "", badParam("note is the one line that says what is missing", "note")
	}
	for i, r := range refs {
		end := len(note)
		if i+1 < len(refs) {
			end = refs[i+1].Start
		}
		if strings.TrimSpace(note[r.End:end]) != "" {
			return "", badParam("note is one line of words, then only its image references", "note")
		}
		lines = append(lines, textsafe.Oneline(note[r.Start:r.End]))
	}
	return strings.Join(lines, "\n"), nil
}

// rejectMethod is task.reject: the report is dropped and the task goes back to its owner's queue, the note told to the owner.
func rejectMethod(home string, tell Teller) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		tid, err := taskID(p)
		if err != nil {
			return nil, err
		}
		note, _ := p["note"].(string)
		if note, err = rejectNote(note); err != nil {
			return nil, err
		}
		by := c.Caller.Actor()
		var notes []told
		sent := false
		out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			t, err := reportFor(tx, home, tid, by, "reject", 0)
			if err != nil {
				return nil, err
			}
			if err := claimImages(tx, home, by, note, "", "note"); err != nil {
				return nil, err
			}
			now := time.Now().Unix()
			if _, err := tx.Exec("UPDATE tasks SET reported_at = NULL, report = NULL, report_via = NULL, accepted_by = NULL, accepted_at = NULL, queued_by = COALESCE(queued_by, ?), "+
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
