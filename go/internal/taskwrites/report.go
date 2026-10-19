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

// A done by an assignee the task does not answer to is an answer: the task stays with its assignee, answered, until whoever it answers to
// accepts it (it closes with the answer as its evidence), rejects it (it goes back to its assignee's queue with a note) or takes it back.

// fileReport is MarkDone for an assignee the task does not answer to, its checks passed: the answer is kept, the assignee's queued
// deliveries close and it is free for its next task, and whoever the task answers to is told how to accept, reject or take it back.
func fileReport(tx *sql.Tx, by string, t *task, mark, via, from string, result taskresult.Result, finalize func() error) (*DoneResult, error) {
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
	to, who := t.reporter(), by
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

// reportFor reads task tid for an accept or a reject by `by`: refused unless a report waits on it and `by` is whoever it answers to, its
// opener or the founder. proof, when not 0, is the founder's chat message that approved it (founderProof): with it, a report that
// answers to the founder may also be accepted by the lead of its owner or its opener.
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
	if proof != 0 {
		if err := founderProof(tx, t, proof); err != nil {
			return nil, err
		}
	}
	if by == t.reporter() || by == t.OpenedBy || by == human {
		return t, nil
	}
	if proof != 0 && t.reporter() == human {
		for _, a := range []string{t.Owner, t.OpenedBy} {
			if lead, err := leadOf(home, a); err != nil {
				return nil, err
			} else if lead != "" && lead == by {
				return t, nil
			}
		}
	}
	return nil, refusal(fmt.Sprintf("task T%d's answer can only be %sed by %s - whoever it answers to - or the founder", tid, verb, t.reporter()))
}

// leadOf is who agent answers to: its team's manager, else the agent its name hangs under; "" for none, or with no team.yaml.
func leadOf(home, agent string) (string, error) {
	tm, err := team.LoadTeam(home)
	if errors.Is(err, os.ErrNotExist) {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	if m := tm.ManagerOf(agent); m != "" {
		return m, nil
	}
	return tm.SupervisorOf(agent), nil
}

// founderProof is the founder's chat message mid kept as the proof of an accept: refused unless the founder wrote it, to this task's
// report or later, tied to the task itself or as a reply to a message that is.
func founderProof(tx *sql.Tx, t *task, mid int64) error {
	var from string
	var at int64
	err := tx.QueryRow("SELECT r.requester, r.ts FROM requests r WHERE r.id = ? AND EXISTS (SELECT 1 FROM task_requests l "+
		"WHERE l.task_id = ? AND l.request_id IN (r.id, r.parent_id))", mid, t.ID).Scan(&from, &at)
	if err == sql.ErrNoRows {
		return refusal(fmt.Sprintf("message #%d is not tied to task T%d", mid, t.ID))
	}
	if err != nil {
		return err
	}
	if from != human {
		return refusal(fmt.Sprintf("message #%d is from %s, not the founder", mid, from))
	}
	if at < t.ReportedAt.Int64 {
		return refusal(fmt.Sprintf("message #%d was sent before T%d was answered", mid, t.ID))
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
		out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			mid, byFounder := rpc.AsInt(p["by_founder"])
			if p["by_founder"] != nil && (!byFounder || mid <= 0) {
				return nil, badParam("by_founder is a message id", "by_founder")
			}
			t, err := reportFor(tx, home, tid, by, "accept", mid)
			if err != nil {
				return nil, err
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
		return out, err
	}
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
