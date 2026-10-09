package reads

import (
	"fmt"
	"time"
)

// defaultTaskTimeoutS is actions.DEFAULT_TASK_TIMEOUT_S: the length of a task never given one.
const defaultTaskTimeoutS = 4 * 3600

// taskClock is the spec's TaskClock: the task's time box, null once it is done or discarded. The overdue moment is the first deadline
// missed, found by following the overrun notices back from the deadline now in force (an edit replaces the deadline and ends the chain).
func (e *Env) taskClock(t row, state string) (any, error) {
	if state == "done" || state == "discarded" {
		return nil, nil
	}
	length := t.i("timeout_s")
	isDefault := !t.set("timeout_s")
	if isDefault || length == 0 {
		length = defaultTaskTimeoutS
	}
	clock := map[string]any{"length_s": length, "default": isDefault, "start": nil, "end": nil, "overdue_since": nil, "told": 0}
	if state != "doing" || !t.set("timeout_at") {
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
