package taskwrites

import (
	"database/sql"
	"fmt"
	"strconv"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/housekeeping"
	"github.com/handline/handline/go/internal/rpc"
)

// laterItem is the {kind, id} item of later.set and later.clear: an id may come as text or as a number.
func laterItem(p map[string]any) (kind, id string, err error) {
	it, ok := p["item"].(map[string]any)
	if !ok {
		return "", "", badParam("item is {kind, id}", "item")
	}
	kind, _ = it["kind"].(string)
	switch v := it["id"].(type) {
	case string:
		id = v
	case interface{ String() string }: // json.Number
		id = v.String()
	}
	switch kind {
	case db.LaterTask, db.LaterComment, db.LaterMachine:
	case "session", "hr":
		return "", "", rpc.Refused("a session's ask and an HR proposal are not deferred: an agent is stopped on them", "", "")
	default:
		return "", "", badParam("item.kind is task, comment or machine", "item")
	}
	if id == "" {
		return "", "", badParam("item.id is the task or comment number, or disk", "item")
	}
	return kind, id, nil
}

func laterNotFound(what, id string) error {
	e := rpc.NotFoundError(fmt.Sprintf("no open %s %s to defer", what, id))
	e.Data = map[string]any{"what": what, "name": id}
	return e
}

// laterOwnTask is the task the item belongs to (0 for the machine's), after checking that the item is open now.
func laterOwnTask(q db.Q, kind, id string) (int64, error) {
	switch kind {
	case db.LaterTask:
		tid, err := strconv.ParseInt(id, 10, 64)
		if err != nil {
			return 0, badParam("a task item's id is the task number", "item")
		}
		switch end, found, err := db.TaskEnd(q, tid); {
		case err != nil:
			return 0, err
		case !found:
			return 0, laterNotFound("task", id)
		case end != "":
			return 0, rpc.Refused(fmt.Sprintf("T%d is %s: nothing to defer", tid, end), "", "")
		}
		return tid, nil
	case db.LaterComment:
		mid, err := strconv.ParseInt(id, 10, 64)
		if err != nil {
			return 0, badParam("a comment item's id is the comment number", "item")
		}
		tid, ok, err := db.CommentAskTask(q, mid)
		if err != nil {
			return 0, err
		}
		if !ok {
			return 0, laterNotFound("comment", id)
		}
		return tid, nil
	}
	if _, _, ok := housekeeping.DiskNeed(q); id != "disk" || !ok {
		return 0, laterNotFound("machine", id)
	}
	return 0, nil
}

// laterSet is later.set. The founder's alone.
func laterSet(c *rpc.Conn, p map[string]any) (any, error) {
	kind, id, err := laterItem(p)
	if err != nil {
		return nil, err
	}
	rawUntil, hasUntil := p["until"]
	rawAfter, hasAfter := p["after_task"]
	if hasUntil == hasAfter {
		return nil, badParam("give exactly one of until and after_task", "until")
	}
	var until, after *int64
	if hasUntil {
		s, _ := rawUntil.(string)
		t, perr := time.Parse(time.RFC3339, s)
		if perr != nil {
			return nil, badParam("until is a time, RFC 3339", "until")
		}
		u := t.Unix()
		until = &u
	} else {
		n, ok := rpc.AsInt(rawAfter)
		if !ok {
			return nil, badParam("after_task is a task number", "after_task")
		}
		after = &n
	}
	return c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		own, err := laterOwnTask(tx, kind, id)
		if err != nil {
			return nil, err
		}
		now := time.Now().Unix()
		if until != nil && *until <= now {
			return nil, rpc.Refused("until is not in the future", "", "")
		}
		if after != nil {
			end, found, err := db.TaskEnd(tx, *after)
			switch {
			case err != nil:
				return nil, err
			case !found:
				return nil, refusal(fmt.Sprintf("no task T%d", *after))
			case end != "":
				return nil, rpc.Refused(fmt.Sprintf("T%d is already %s: there is nothing to wait for", *after, end), "", "")
			case *after == own:
				return nil, rpc.Refused("an item cannot wait for its own task", "", "")
			}
		}
		if err := db.LaterSet(tx, kind, id, until, after, now); err != nil {
			return nil, err
		}
		return accepted(fmt.Sprintf("%s %s deferred", kind, id), nil), nil
	})
}

// laterClear is later.clear.
func laterClear(c *rpc.Conn, p map[string]any) (any, error) {
	kind, id, err := laterItem(p)
	if err != nil {
		return nil, err
	}
	return c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		if ok, err := db.LaterClear(tx, kind, id); err != nil {
			return nil, err
		} else if !ok {
			e := rpc.NotFoundError(fmt.Sprintf("%s %s is not deferred", kind, id))
			e.Data = map[string]any{"what": "later", "name": id}
			return nil, e
		}
		return accepted(fmt.Sprintf("%s %s returned", kind, id), nil), nil
	})
}
