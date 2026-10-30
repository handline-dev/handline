package reads

import (
	"strconv"
	"time"

	"github.com/handline/handline/go/internal/db"
)

// laterOf is the spec's Later of a For you item while the founder has it deferred, nil when not: the item is out of its group until the
// time, or until the task is done or discarded, even before the watcher has deleted the row.
func (e *Env) laterOf(kind string, id int64) (map[string]any, error) {
	return e.laterOfID(kind, strconv.FormatInt(id, 10))
}

func (e *Env) laterOfID(kind, id string) (map[string]any, error) {
	l, err := db.LaterOf(e.DB, kind, id, time.Now().Unix())
	if err != nil || l == nil {
		return nil, err
	}
	if l.Until.Valid {
		return map[string]any{"until": ts(l.Until.Int64)}, nil
	}
	return map[string]any{"after_task": l.After.Int64}, nil
}
