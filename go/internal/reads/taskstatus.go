package reads

import (
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/status"
	"github.com/handline/handline/go/internal/supervisor"
)

// statusOf is the spec's Task.status: its state on the board, and for an open task the cause waitingOf found.
func (e *Env) statusOf(t row, state string, waiting map[string]any, since int64) (status.Status, error) {
	owner := t.str("owner")
	at := func(st, reason, text string, since int64) status.Status {
		return status.Status{State: st, Reason: reason, Text: text, Since: since}
	}
	switch state {
	case "done":
		return at(status.Done, status.Done, "done", t.i("done_at")), nil
	case "discarded":
		return at(status.Done, status.Discarded, "discarded: "+t.str("discard_reason"), t.i("discarded_at")), nil
	case "draft":
		return at(status.Waiting, status.Opener, "draft: "+t.str("opened_by")+" has not queued it", t.i("created_at")), nil
	}
	if waiting == nil {
		switch {
		case owner == db.Gate:
			text := "watching " + t.str("watch_label")
			if why := t.str("watch_error"); why != "" {
				text += " - last read failed: " + why
			}
			return at(status.Running, status.Watch, text, t.i("sent_at")), nil
		case owner == human:
			return at(status.Waiting, status.You, "yours to do", t.i("created_at")), nil
		case state == "queued":
			return at(status.Preparing, status.Work, owner+" gets it at the next tick", t.i("created_at")), nil
		}
		return at(status.Running, status.Turn, owner+" has it", t.i("delivered_at")), nil
	}
	text, _ := waiting["text"].(string)
	switch waiting["on"] {
	case "task":
		s := at(status.Waiting, status.Task, text, since)
		s.Tasks, _ = waiting["tasks"].([]int64)
		return s, nil
	case "founder":
		return at(status.Waiting, status.You, text, since), nil
	case "cap":
		return at(status.Stopped, status.Cap, text, since), nil
	case "wall":
		s := at(status.Stopped, status.Wall, text, since)
		if e.Delivery != nil {
			row, err := db.GetAgentState(e.DB, owner)
			if err != nil {
				return s, err
			}
			if w, err := e.Delivery.Wall(e.DB, owner, row); err != nil {
				return s, err
			} else if w != nil {
				s.Until = w.Clock
			}
		}
		return s, nil
	}
	if text == supervisor.Ready {
		return at(status.Preparing, status.Work, owner+" gets it at the next tick", since), nil
	}
	return at(status.Waiting, status.Owner, text, since), nil
}
