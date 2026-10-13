package reads

import (
	"fmt"
	"time"

	"github.com/handline/handline/go/internal/db"
)

// taskClock is the spec's TaskClock: the task's time box, null once it is done or discarded; it runs while doing and while reported, and while it is
// paused left_s is what it had left. start is end less the length, so a clock that paused and resumed reads as begun later than it was. The overdue moment is the first deadline
// missed, found by following the overrun notices back from the deadline now in force (an edit replaces the deadline and ends the chain).
func (e *Env) taskClock(t row, state string) (any, error) {
	if state == "done" || state == "discarded" {
		return nil, nil
	}
	length := t.i("timeout_s")
	isDefault := !t.set("timeout_s")
	if isDefault || length == 0 {
		length = db.DefaultTaskTimeoutS
	}
	clock := map[string]any{"length_s": length, "default": isDefault, "start": nil, "end": nil, "overdue_since": nil, "told": 0, "left_s": nil}
	if !t.set("timeout_at") {
		if t.set("timeout_left") {
			clock["left_s"] = t.i("timeout_left")
		}
		return clock, nil
	}
	if state != "doing" && state != "reported" {
		return clock, nil
	}
	now, end := time.Now().Unix(), t.i("timeout_at")
	clock["start"], clock["end"] = ts(end-length), ts(end)
	notices, err := query(e.DB, fmt.Sprintf("SELECT payload FROM events WHERE kind = 'task_timeout_exceeded' AND %s = ? ORDER BY id DESC", eventTask), t.i("id"))
	if err != nil {
		return nil, err
	}
	first, told, want := int64(0), 0, end
	for _, n := range notices {
		p := decodeObject(n.str("payload"))
		deadline, ok := p["deadline"].(interface{ Int64() (int64, error) })
		next, ok2 := p["next"].(interface{ Int64() (int64, error) })
		if !ok || !ok2 {
			continue
		}
		d, _ := deadline.Int64()
		if nx, _ := next.Int64(); nx != want {
			break
		}
		first, told, want = d, told+1, d
	}
	if first == 0 && end <= now {
		first = end
	}
	if first != 0 {
		clock["overdue_since"], clock["told"] = ts(first), told
	}
	return clock, nil
}
