package supervisor

import (
	"fmt"
	"strconv"
	"strings"

	"github.com/handline/handline/go/internal/db"
)

// A task the board shows as doing (sent, and the one its holder holds) that has not moved for StallS is told to the one it answers to, once per stall. The task clock is not
// read: a clock paused on a wait kept a stalled task quiet for 35 h. Progress is what the daemon already records: a state change on the task
// row, a memo, a progress line, a task event (a handoff, a block, a report), and the holder's own turns while the task is the one it holds.
// The board is read every StallCheckEveryS, not every tick.

const (
	// StallS: this long with no progress is a stall.
	StallS = 4 * 3600
	// StallCheckEveryS: how often the board is read for stalls.
	StallCheckEveryS = 600
	// StallPrefix names the meta rows that keep, per stalled task, the progress time its alert was for.
	StallPrefix   = "taskstall:"
	stallCheckKey = "stall-check"
)

// progressKinds are the task events that are someone moving the task. The daemon's own notes (a timeout, an end told, a refused delivery)
// are not.
var progressKinds = []string{"task_new", "task_start", "task_begin", "task_handoff", "edit_task", "block_task", "unblock_task", "task_unblocked",
	"dormant_block", "task_report", "reject_task", "release_task", "queue_task", "draft_task", "reopen_task", "child_result_told"}

// turnKinds are the holder's own turns.
var turnKinds = []string{"UserPromptSubmit", "Stop"}

func stallKey(tid int64) string { return fmt.Sprintf("%s%d", StallPrefix, tid) }

// SweepStalls tells the one a doing task answers to when the task has had no progress for StallS. A task that moves again is cleared, and
// stalls again only after another StallS.
func (e *Env) SweepStalls(now int64) (err error) {
	defer Catch(&err)
	q := db.Q(e.DB)
	if now-must(db.Cursor(q, stallCheckKey)) < StallCheckEveryS {
		return nil
	}
	check(db.SetCursor(q, stallCheckKey, now))
	seen := map[int64]bool{}
	for _, t := range must(db.OpenTasks(q)) {
		if t.Owner == db.Human || must(db.TaskState(q, t)) != "doing" {
			continue
		}
		seen[t.ID] = true
		last := lastProgress(q, t)
		raw, _ := ok2(db.Meta(q, stallKey(t.ID)))
		told, _ := strconv.ParseInt(raw, 10, 64)
		if now-last < StallS || told == last {
			continue
		}
		e.tellStall(q, t, last, now)
		check(db.SetMeta(q, stallKey(t.ID), strconv.FormatInt(last, 10)))
	}
	for _, name := range must(db.MetaNames(q, StallPrefix)) {
		tid, _ := strconv.ParseInt(name[len(StallPrefix):], 10, 64)
		if !seen[tid] {
			check(db.ClearMeta(q, name))
		}
	}
	return nil
}

// tellStall writes the alert to the one the task answers to: the founder through a report, an agent through a say.
func (e *Env) tellStall(q db.Q, t *db.Task, last, now int64) {
	holder := t.Owner
	text := fmt.Sprintf("T%d (%s) is doing with no progress for %dh: no memo, progress line, state change or handoff, and %s has taken no "+
		"turn on it. Ask %s about it, or move or release the task.", t.ID, t.Title, (now-last)/3600, holder, holder)
	to := t.AnswersTo()
	must(db.AddEvent(q, holder, "task_stalled", db.KV{K: "task_id", V: t.ID}, db.KV{K: "told", V: to}))
	if to == db.Human {
		after := &After{}
		e.report(q, after, holder, text, "doing task stalled")
		after.Run()
	} else {
		e.typeSay(q, to, text, "doing task stalled")
	}
	e.log("  T%d: no progress for %dh - %s told", t.ID, (now-last)/3600, to)
}

// lastProgress is when the task last moved: the latest of its row's own stamps, its memos, its progress lines, its task events and, while it
// is the one its holder holds, the holder's turns.
func lastProgress(q db.Q, t *db.Task) int64 {
	last := t.CreatedAt
	for _, v := range []struct {
		ok bool
		n  int64
	}{{t.SentAt.Valid, t.SentAt.Int64}, {t.DeliveredAt.Valid, t.DeliveredAt.Int64}, {t.QueuedAt.Valid, t.QueuedAt.Int64}, {t.EditedAt.Valid, t.EditedAt.Int64}} {
		if v.ok && v.n > last {
			last = v.n
		}
	}
	later := func(query string, args ...any) {
		var n int64
		check(q.QueryRow(query, args...).Scan(&n))
		if n > last {
			last = n
		}
	}
	later("SELECT COALESCE(CAST(MAX(COALESCE(edited_at, created_at)) AS INTEGER), 0) FROM task_memos WHERE task_id = ?", t.ID)
	later("SELECT COALESCE(MAX(updated), 0) FROM meta WHERE name = ? OR name IN (SELECT 'progress:' || request_id FROM task_requests WHERE task_id = ?)",
		fmt.Sprintf("progress-task:%d", t.ID), t.ID)
	later("SELECT COALESCE(MAX(ts), 0) FROM events WHERE kind IN ("+marks(len(progressKinds))+") AND "+
		"CASE WHEN json_valid(payload) THEN json_extract(payload, '$.task_id') END = ?", append(anys(progressKinds), t.ID)...)
	if cur, ok := ok2(db.CurrentTask(q, t.Owner)); ok && cur == t.ID {
		later("SELECT COALESCE((SELECT ts FROM events WHERE agent = ? AND kind IN ("+marks(len(turnKinds))+") ORDER BY id DESC LIMIT 1), 0)",
			append([]any{t.Owner}, anys(turnKinds)...)...)
	}
	return last
}

func marks(n int) string { return strings.TrimSuffix(strings.Repeat("?,", n), ",") }

func anys(xs []string) []any {
	out := make([]any, len(xs))
	for i, x := range xs {
		out[i] = x
	}
	return out
}
