package watcher

import (
	"database/sql"
	"fmt"
	"slices"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/stream"
)

// recentS: a task that ended longer ago than this is no longer looked at for change.
const recentS = 86400

const taskPrint = "SELECT t.id, t.title, t.owner, t.opened_by, t.priority, t.sent_at, t.queued_by, t.done_evidence, t.done_at, t.discarded_by, t.discarded_at, " +
	"t.timeout_s, t.parent_task, t.edited_at, t.delivered_at FROM tasks t " +
	"WHERE (t.done_evidence IS NULL AND t.discarded_by IS NULL) OR COALESCE(t.done_at, t.discarded_at, 0) >= ? ORDER BY t.id"

// What else makes a task change, each in one grouped read of its own table rather than a subquery per task.
var taskExtras = []string{
	"SELECT task_id, COUNT(*), MAX(id), MAX(edited_at) FROM task_memos GROUP BY task_id",
	"SELECT l.task_id, COUNT(*), MAX(r.state_at) FROM task_requests l JOIN requests r ON r.id = l.request_id GROUP BY l.task_id",
	"SELECT task_id, COUNT(*) FROM task_blocks WHERE dormant = 0 GROUP BY task_id",
}

// Tasks is the tasks topic: a task that appeared or changed (its fields, a memo, a request linked to it), as the spec's Task. Its
// first look says nothing.
type Tasks struct {
	Reads *reads.Env
	Now   func() time.Time // nil is time.Now
	seen  map[int64]string
}

func (*Tasks) Name() string { return "tasks" }

// rowsPrint reads every row of query as text, keyed by its first column, so two reads are equal exactly when the rows are.
func rowsPrint(q *sql.DB, query string, args ...any) (map[int64]string, error) {
	rows, err := q.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	cols, err := rows.Columns()
	if err != nil {
		return nil, err
	}
	out := map[int64]string{}
	for rows.Next() {
		vals := make([]any, len(cols))
		ptrs := make([]any, len(cols))
		for i := range vals {
			ptrs[i] = &vals[i]
		}
		if err := rows.Scan(ptrs...); err != nil {
			return nil, err
		}
		var b strings.Builder
		for _, v := range vals {
			fmt.Fprintf(&b, "%T:%v|", v, v)
		}
		id, _ := vals[0].(int64)
		out[id] = b.String()
	}
	return out, rows.Err()
}

func (t *Tasks) Scan(q *sql.DB, emit Emit) error {
	now := time.Now
	if t.Now != nil {
		now = t.Now
	}
	print, err := rowsPrint(q, taskPrint, now().Unix()-recentS)
	if err != nil {
		return err
	}
	for _, query := range taskExtras {
		extra, err := rowsPrint(q, query)
		if err != nil {
			return err
		}
		for id := range print {
			print[id] += "#" + extra[id]
		}
	}
	before := t.seen
	t.seen = print
	if before == nil {
		return nil
	}
	for _, id := range sortedIDs(print) {
		if before[id] == print[id] {
			continue
		}
		task, ok, err := t.Reads.Task(id)
		if err != nil {
			return err
		}
		if !ok {
			continue
		}
		owner, _ := task["to"].(string)
		title, _ := task["title"].(string)
		if _, err := emit(stream.Entry{Topic: "tasks", Type: "task.changed", Data: task, Agent: owner, Title: title,
			Target: map[string]any{"screen": "tasks", "id": fmt.Sprint(id)}}); err != nil {
			return err
		}
	}
	return nil
}

func sortedIDs(m map[int64]string) []int64 {
	ids := make([]int64, 0, len(m))
	for id := range m {
		ids = append(ids, id)
	}
	slices.Sort(ids)
	return ids
}
