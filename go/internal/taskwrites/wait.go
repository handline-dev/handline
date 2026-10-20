package taskwrites

import (
	"database/sql"
	"fmt"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
)

// waitMethod is task.wait {task, watch, time_box?}: a gate on watch, the task blocked by it and, when doing, released, all in one
// transaction, so the task never waits on a gate that blocks nothing. It comes back to its holder's queue when the gate opens.
func waitMethod(home string, tell Teller) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		tid, err := taskID(p)
		if err != nil {
			return nil, err
		}
		secs, err := timeBox(p, "time_box")
		if err != nil {
			return nil, err
		}
		if secs == 0 {
			secs = db.GateDeadlineS
		}
		w, err := parseWatch(p["watch"])
		if err != nil {
			return nil, err
		}
		if why, err := waitRefusal(c.Server().DB, tid); err != nil || why != "" {
			if err == nil {
				err = refusal(why)
			}
			return nil, err
		}
		if err := watchReady(w); err != nil { // last: a vendor may read the outside to say it is set up
			return nil, err
		}
		me := c.Caller.Actor()
		var (
			r     released
			gate  int64
			freed bool
		)
		out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			r, freed = released{}, false
			var err error
			gate, _, err = OpenTask(tx, tell, me, NewIn{Owner: db.Gate, Title: fmt.Sprintf("T%d waits on %s", tid, w.Label), Secs: secs,
				Prio: db.DefaultPriority, Expects: "report", Watch: w})
			if err != nil {
				return nil, err
			}
			if why, err := recordBlock(tx, tid, gate, me); err != nil || why != "" {
				if err == nil {
					err = refusal(why)
				}
				return nil, err
			}
			said := fmt.Sprintf("T%d waits on gate T%d (watching %s)", tid, gate, w.Label)
			t, err := getTask(tx, tid)
			if err != nil {
				return nil, err
			}
			if st, err := state(tx, t); err != nil {
				return nil, err
			} else if st == "doing" {
				if r, err = releaseTask(tx, home, me, tid, fmt.Sprintf("waits on gate T%d", gate)); err != nil {
					return nil, err
				}
				freed = true
				said += "; " + r.said()
			}
			return accepted(said, map[string]any{"gate": gate}), nil
		})
		if err == nil && tell != nil && freed {
			r.tell(tell, tid)
		}
		return out, err
	}
}

// waitRefusal is why task tid cannot wait, "" when it can: it must be there and open.
func waitRefusal(sdb *sql.DB, tid int64) (string, error) {
	tx, err := sdb.Begin()
	if err != nil {
		return "", err
	}
	defer tx.Rollback()
	t, err := getTask(tx, tid)
	switch {
	case err != nil:
		return "", err
	case t == nil:
		return fmt.Sprintf("no task T%d", tid), nil
	case t.ended():
		return fmt.Sprintf("task T%d is %s", tid, t.endWord()), nil
	}
	return "", nil
}
