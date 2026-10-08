package taskwrites

import (
	"database/sql"
	"fmt"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/textsafe"
)

// Asked is the answer to a handoff's ask. A refusal has OK false and its writes (a denied row) stand. After runs once the transaction has
// committed (the inbox file, the telling) and answers a line for the reply.
type Asked struct {
	OK    bool
	Msg   string
	RID   int64
	After func() string
}

// AskReq is a task's delivery to be routed. Kind "" is the routing edge's own; NoCard keeps a request to the founder from raising a decision
// card (task.new opens its own task); Notify types the target after the commit, which a handoff leaves to HandedOver.
type AskReq struct {
	From, To, Body, Kind string
	NoCard, Notify       bool
	Within               func(rid int64) error
}

// Handover is a handoff that committed.
type Handover struct {
	Task         int64
	By, From, To string
	Title        string
	RID          int64
	WasCurrent   bool // the old owner held it as its current task
	Retired      []func()
	After        func() string
}

// handoffRefusal is why `me` may not hand the task to `to`, or "": the owner (or the founder) hands it on, with a note.
func handoffRefusal(t *task, me, to, note string, agents []string) string {
	switch {
	case t.done() || t.DiscardedBy.Valid && t.DiscardedBy.String != "":
		return fmt.Sprintf("task T%d is already finished - nothing to hand over", t.ID)
	case note == "":
		return "a handoff needs a note: what changed, and what the receiver should know"
	case to != human && !contains(agents, to):
		return "no agent named " + to
	case to == t.Owner:
		return fmt.Sprintf("T%d is already %s's", t.ID, to)
	case me != human && me != t.Owner:
		return fmt.Sprintf("T%d is %s's: only its owner hands it on", t.ID, t.Owner)
	}
	return ""
}

func contains(xs []string, x string) bool {
	for _, v := range xs {
		if v == x {
			return true
		}
	}
	return false
}

// handoffMethod is task.handoff (actions.task_handoff): the owner passes a task along with a note. The ask to the new owner, the move and the
// record of the result are one transaction, so a crash between them leaves nothing half done and a retry needs no intent to find its way.
func handoffMethod(home string, tell Teller) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		tid, err := taskID(p)
		if err != nil {
			return nil, err
		}
		note, err := text(p, "note")
		if err != nil {
			return nil, err
		}
		to, err := text(p, "to")
		if err != nil {
			return nil, err
		}
		agents := paths.ListAgents(home)
		if to != human && !contains(agents, to) {
			e := rpc.NotFoundError("no agent named " + to)
			e.Data = map[string]any{"what": "agent", "name": to}
			return nil, e
		}
		me := c.Caller.Actor()
		said := strings.TrimSpace(textsafe.Text(note))
		var (
			h     Handover
			asked *Asked
			msg   string
		)
		out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			h, asked = Handover{}, nil
			t, err := getTask(tx, tid)
			if err != nil {
				return nil, err
			}
			if t == nil {
				return nil, refusal(fmt.Sprintf("no task T%d", tid))
			}
			if why := handoffRefusal(t, me, to, said, agents); why != "" {
				return nil, refusal(why)
			}
			h = Handover{Task: tid, By: me, From: t.Owner, To: to, Title: t.Title}
			if tell == nil {
				return nil, fmt.Errorf("task.handoff needs the supervisor to route its ask")
			}
			asked, err = tell.TaskAsk(tx, AskReq{From: me, To: to, Kind: "task", Body: fmt.Sprintf("T%d handed to you by %s: %s\n\n%s", tid, me, t.Title, said),
				Within: func(rid int64) error {
					h.RID = rid
					return moveTask(tx, tell, &h, said)
				}})
			if err != nil {
				return nil, err
			}
			if !asked.OK {
				return nil, &rpc.CommitThen{Err: refusal(asked.Msg)}
			}
			msg = asked.Msg
			return accepted(fmt.Sprintf("T%d handed to %s: %s", tid, to, msg), nil), nil
		})
		if asked != nil && !asked.OK && asked.After != nil {
			asked.After() // the refusal's writes stood: what follows them still runs
		}
		if err != nil {
			return nil, err
		}
		h.After = asked.After
		if tail := tell.HandedOver(h); tail != "" {
			out["said"] = fmt.Sprintf("T%d handed to %s: %s\n%s", tid, to, msg, tail)
		}
		return out, nil
	}
}

// moveTask is what follows an allowed ask, in its transaction: the old owner's open requests close, the task changes hands and links the
// delivery.
func moveTask(tx *sql.Tx, tell Teller, h *Handover, note string) error {
	reqs, err := taskRequests(tx, h.Task)
	if err != nil {
		return err
	}
	for _, r := range reqs {
		if r.State == "queued" && r.Target == h.From && r.ID != h.RID {
			told, err := tell.HandoffRetire(tx, r.ID, "handed to "+h.To)
			if err != nil {
				return err
			}
			if told != nil {
				h.Retired = append(h.Retired, told)
			}
		}
	}
	if err := db.EndHeld(tx, h.Task, h.From); err != nil {
		return err
	}
	if err := db.EditTask(tx, h.Task, h.By, db.TaskEdit{Owner: &h.To}); err != nil {
		return err
	}
	went, err := db.SyncSelfBlocks(tx, h.Task, h.From)
	if err != nil {
		return err
	}
	for _, w := range went {
		if err := addEvent(tx, h.By, "dormant_block", kv{"task_id", w[0]}, kv{"blocks_on", w[1]}, kv{"why", "one agent holds both"}); err != nil {
			return err
		}
	}
	if err := db.LinkRequest(tx, h.Task, h.RID); err != nil {
		return err
	}
	if _, err := db.MarkSendNow(tx, h.RID); err != nil { // a handoff is typed on arrival, whatever its holder is doing
		return err
	}
	at := float64(time.Now().UnixNano()) / 1e9
	if err := addEvent(tx, h.By, "task_handoff", kv{"task_id", h.Task}, kv{"from", h.From}, kv{"to", h.To}, kv{"note", note},
		kv{"request_id", h.RID}, kv{"at", at}); err != nil {
		return err
	}
	if cur, held, err := db.CurrentTask(tx, h.From); err != nil {
		return err
	} else if held && cur == h.Task {
		h.WasCurrent = true
		if err := db.SetCurrentTask(tx, h.From, 0, false); err != nil {
			return err
		}
	}
	if h.To == human { // a task the founder holds is asked of them, so it is queued: a draft is outside "for you"
		_, err := db.SetTaskQueued(tx, h.Task, h.By)
		return err
	}
	if _, held, err := db.CurrentTask(tx, h.To); err != nil {
		return err
	} else if !held {
		return db.SetCurrentTask(tx, h.To, h.Task, true)
	}
	return nil
}
