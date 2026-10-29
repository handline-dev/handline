package watcher

import (
	"database/sql"
	"fmt"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/housekeeping"
	"github.com/handline/handline/go/internal/stream"
)

// Later returns what the founder deferred (the later table). A deferral ends when its time passes or the task it waits for is done or
// discarded: the row is deleted and a later_returned event on the tasks topic tells the apps, so a list needs no timer of its own. A
// row whose item is gone (the task ended, the comment's ask answered, the disk need cleared) is deleted without a word: the item's own
// change said it.
type Later struct {
	Now func() time.Time // nil is time.Now

	afterRead func() // test hook: runs between reading the rows and ending any of them
}

func (*Later) Name() string { return "later" }

// LaterSignature says when a deferral's time has passed: the database does not change then, so the watch would not look.
func LaterSignature(q *sql.DB) func() string {
	return func() string {
		var n int64
		if err := q.QueryRow("SELECT COUNT(*) FROM later WHERE until_at IS NOT NULL AND until_at <= ?", time.Now().Unix()).Scan(&n); err != nil {
			return ""
		}
		return fmt.Sprint(n)
	}
}

// goneItem says whether the deferred item no longer exists as one for the founder.
func goneItem(q db.Q, l db.Later) (bool, error) {
	switch l.Kind {
	case db.LaterTask:
		var tid int64
		if _, err := fmt.Sscan(l.ID, &tid); err != nil {
			return true, nil
		}
		end, found, err := db.TaskEnd(q, tid)
		return !found || end != "", err
	case db.LaterComment:
		var mid int64
		if _, err := fmt.Sscan(l.ID, &mid); err != nil {
			return true, nil
		}
		_, ok, err := db.CommentAskTask(q, mid)
		return !ok, err
	}
	_, _, ok := housekeeping.DiskNeed(q)
	return !ok || l.ID != "disk", nil
}

func (w *Later) Scan(q *sql.DB, emit Emit) error {
	now := time.Now
	if w.Now != nil {
		now = w.Now
	}
	rows, err := db.LaterRows(q)
	if err != nil {
		return err
	}
	if w.afterRead != nil {
		w.afterRead()
	}
	for _, l := range rows {
		gone, err := goneItem(q, l)
		if err != nil {
			return err
		}
		why := ""
		if !gone {
			if why, err = l.Over(q, now().Unix()); err != nil {
				return err
			}
			if why == "" {
				continue
			}
		}
		// a clear or a new set that came after the read took the row: it is not this scan's to end or tell
		if dropped, err := db.LaterDrop(q, l); err != nil {
			return err
		} else if !dropped || gone {
			continue
		}
		entry := stream.Entry{Topic: "tasks", Type: "later_returned", Data: map[string]any{"item": map[string]any{"kind": l.Kind, "id": l.ID}, "why": why}}
		if l.Kind == db.LaterTask {
			entry.Target = map[string]any{"screen": "tasks", "id": l.ID}
		}
		if _, err := emit(entry); err != nil {
			return err
		}
	}
	return nil
}
