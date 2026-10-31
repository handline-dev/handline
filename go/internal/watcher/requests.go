package watcher

import (
	"database/sql"
	"time"

	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/stream"
)

// Requests is the requests topic: request.changed with the RequestRow of each request that appeared or changed. Its first look says nothing.
type Requests struct {
	Reads *reads.Env
	Now   func() time.Time // nil is time.Now
	seen  map[string]string
}

func (*Requests) Name() string { return "requests" }

func (*Requests) Tables() []string {
	return []string{"events", "requests", "task_blocks", "task_memos", "task_requests", "tasks"}
}

func (r *Requests) Scan(_ *sql.DB, emit Emit) error {
	now := time.Now
	if r.Now != nil {
		now = r.Now
	}
	rows, err := r.Reads.RequestRows(now().Unix())
	if err != nil {
		return err
	}
	before := r.seen
	r.seen = map[string]string{}
	for _, row := range rows {
		key := encoded(row["task"]) + "/" + encoded(row["ref"])
		r.seen[key] = encoded(row)
		if before == nil || before[key] == r.seen[key] {
			continue
		}
		if _, err := emit(stream.Entry{Topic: "requests", Type: "request.changed", Data: row, Title: row["title"].(string),
			Target: map[string]any{"screen": "requests"}}); err != nil {
			return err
		}
	}
	return nil
}
