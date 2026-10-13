package taskwrites

import (
	"database/sql"
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/taskresult"
	"github.com/handline/handline/go/internal/textsafe"
	"github.com/handline/handline/go/internal/timefmt"
)

// unserved are the parameters the contract names and no action is behind yet.
func unserved(p map[string]any, keys ...string) error {
	for _, k := range keys {
		if p[k] != nil {
			return badParam(k+" is not served yet", k)
		}
	}
	return nil
}

// timeBox is rpc_tasks._span: the task timeout under key in seconds, 0 when it is not given.
func timeBox(p map[string]any, key string) (int64, error) {
	if p[key] == nil {
		return 0, nil
	}
	v, ok := p[key].(string)
	if !ok {
		return 0, badParam(key+" is text", key)
	}
	secs, err := timefmt.ParseSpan(v)
	if err == nil && secs > db.MaxTaskTimeoutS {
		err = fmt.Errorf("a task timeout is at most %s (%s is longer)", timefmt.Span(db.MaxTaskTimeoutS, false), v)
	}
	if err != nil {
		return 0, badParam(err.Error(), key)
	}
	return secs, nil
}

// optionalTask is rpc_tasks._task_number: a task number, or none.
func optionalTask(p map[string]any, key string) (int64, bool, error) {
	if p[key] == nil {
		return 0, false, nil
	}
	n, ok := rpc.AsInt(p[key])
	if !ok {
		return 0, false, badParam(key+" is a task number", key)
	}
	return n, true, nil
}

func agentNotFound(name string) error {
	e := rpc.NotFoundError("no agent named " + name)
	e.Data = map[string]any{"what": "agent", "name": name}
	return e
}

// newMethod is task.new (actions.task_new): the task is opened and its owner asked in one call, through the routing table. The delivery, the
// task row, its link and the record of the result are one transaction, so a refused owner leaves no task row behind.
func newMethod(home string, tell Teller) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		if err := unserved(p, "relation", "response", "reply", "waits_on"); err != nil {
			return nil, err
		}
		owner, err := text(p, "owner")
		if err != nil {
			return nil, err
		}
		title, err := text(p, "title")
		if err != nil {
			return nil, err
		}
		body := ""
		if p["body"] != nil {
			b, ok := p["body"].(string)
			if !ok {
				return nil, badParam("body is text", "body")
			}
			if strings.TrimSpace(b) == "" {
				return nil, badParam("body is text and not empty", "body")
			}
			body = strings.TrimSpace(textsafe.Text(b))
		}
		secs, err := timeBox(p, "time_box")
		if err != nil {
			return nil, err
		}
		if secs == 0 {
			return nil, badParam("time_box is required", "time_box")
		}
		if owner != human && !contains(paths.ListAgents(home), owner) {
			return nil, agentNotFound(owner)
		}
		reporter := ""
		if p["reporter"] != nil {
			if reporter, err = text(p, "reporter"); err != nil {
				return nil, err
			}
			if reporter != human && !contains(paths.ListAgents(home), reporter) {
				return nil, agentNotFound(reporter)
			}
		}
		prio := int64(db.DefaultPriority)
		if p["priority"] != nil {
			if prio, err = parsePriority(p["priority"]); err != nil {
				return nil, err
			}
		}
		expects := ""
		if p["expects"] != nil {
			if expects, err = parseExpects(p["expects"]); err != nil {
				return nil, err
			}
		}
		ui := false
		if v := p["ui"]; v != nil {
			b, isBool := v.(bool)
			if !isBool {
				return nil, badParam("ui is true or false", "ui")
			}
			ui = b
		}
		parent, hasParent, err := optionalTask(p, "parent")
		if err != nil {
			return nil, err
		}
		blocker, hasBlocker, err := optionalTask(p, "blocked_by")
		if err != nil {
			return nil, err
		}
		me := c.Caller.Actor()
		if expects == "" {
			expects = "report"
			if owner == human && me != human { // a task given to the founder is a decision unless it says otherwise
				expects = "decision"
			}
		}
		clean := strings.TrimSpace(textsafe.Oneline(title))
		var (
			tid   int64
			asked *Asked
		)
		out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			var err error
			tid, asked, err = OpenTask(tx, tell, me, NewIn{Owner: owner, Title: clean, Body: body, Secs: secs, Prio: prio, Expects: expects,
				Parent: parent, HasParent: hasParent, Blocker: blocker, HasBlocker: hasBlocker, Reporter: reporter, UI: ui})
			if err != nil {
				return nil, err
			}
			if !asked.OK {
				return nil, &rpc.CommitThen{Err: refusal(asked.Msg)}
			}
			return accepted(fmt.Sprintf("task T%d opened for %s", tid, owner), map[string]any{"task": tid}), nil
		})
		if asked != nil && !asked.OK && asked.After != nil {
			asked.After() // the refusal's writes stood: what follows them still runs
		}
		if err != nil {
			return nil, err
		}
		limit := ""
		if secs != 0 {
			limit = ", timeout " + timefmt.Span(secs, false)
		}
		msg := asked.Msg
		if owner == human { // the one line the telling gives a founder's task needs no session
			msg += "\nit is on the founder's screen (`hl`)"
		}
		if asked.After != nil {
			// The inbox file and the telling can start a closed session and wait on it: the task is open, so the call returns. A withdraw right
			// after may forget the inbox file before this writes it; an orphan file for a closed request is left, as it is low impact.
			go asked.After()
		}
		out["said"] = fmt.Sprintf("task T%d opened for %s (P%d, expects %s%s): %s", tid, owner, prio, expects, limit, msg)
		return out, nil
	}
}

// NewIn is what OpenTask opens: the fields task.new has checked. Owner is an agent or the founder; Expects and Prio are set.
type NewIn struct {
	Owner, Title, Body string
	Reporter           string // who the task answers to; "" is the opener
	Secs, Prio         int64
	Expects            string
	Parent, Blocker    int64
	HasParent          bool
	HasBlocker         bool
	UI                 bool // the task shows something on a screen (a label; done never requires a screenshot)
}

// OpenTask is actions.task_new in the caller's transaction: the owner is asked through the routing table and the task row, its link and
// the event land with the delivery. A refusal before anything is opened is the error; an owner the routing refuses is asked.OK false, with
// the writes of the refusal standing, and the caller decides how to commit them. The caller calls tell.Opened once it has committed.
func OpenTask(tx *sql.Tx, tell Teller, me string, in NewIn) (tid int64, asked *Asked, err error) {
	owner, body, prio, expects, secs := in.Owner, in.Body, in.Prio, in.Expects, in.Secs
	parent, hasParent, blocker, hasBlocker := in.Parent, in.HasParent, in.Blocker, in.HasBlocker
	clean := in.Title
	if hasBlocker { // refused before anything is opened, as the command does
		if why, err := blockerRefusal(tx, blocker); err != nil {
			return 0, nil, err
		} else if why != "" {
			return 0, nil, refusal(why)
		}
	}
	if body == "" {
		return 0, nil, refusal("a task needs a body: the work itself, what done looks like - the title alone is not a brief")
	}
	if hasParent {
		if ok, err := hasTask(tx, parent); err != nil {
			return 0, nil, err
		} else if !ok {
			return 0, nil, refusal(fmt.Sprintf("no task T%d", parent))
		}
	}
	kind := "task"
	if owner == human {
		kind = ""
	}
	open := func(rid int64) error { // rid 0: a todo the founder opens for themself, which asks no one
		var err error
		reporter := in.Reporter
		if reporter == me {
			reporter = ""
		}
		if tid, err = db.NewTask(tx, clean, owner, me, parent, prio, expects, secs, reporter); err != nil {
			return err
		}
		if in.UI {
			if err := taskresult.MarkUI(tx, tid); err != nil {
				return err
			}
		}
		if me == human { // the founder's own task needs no one's approval
			if _, err := db.SetTaskQueued(tx, tid, human); err != nil {
				return err
			}
		}
		var asking any
		if rid != 0 {
			if err := db.LinkRequest(tx, tid, rid); err != nil {
				return err
			}
			asking = rid
		}
		if _, held, err := db.CurrentTask(tx, owner); err != nil {
			return err
		} else if owner != human && !held {
			if err := db.SetCurrentTask(tx, owner, tid, true); err != nil {
				return err
			}
		}
		if err := addEvent(tx, me, "task_new", kv{"task_id", tid}, kv{"request_id", asking}, kv{"owner", owner}, kv{"expects", expects}); err != nil {
			return err
		}
		if hasParent { // a child task always blocks its parent
			if err := db.AddBlock(tx, parent, tid); err != nil {
				return err
			}
			if err := addEvent(tx, me, "block_task", kv{"task_id", parent}, kv{"blocks_on", tid}); err != nil {
				return err
			}
		}
		if hasBlocker { // a wait the new task cannot keep (a loop through its parent) is dropped, as the command drops it
			if _, err := recordBlock(tx, tid, blocker, me); err != nil {
				return err
			}
		}
		return nil
	}
	if me == human && owner == human {
		if err := open(0); err != nil {
			return 0, nil, err
		}
		return tid, &Asked{OK: true, Msg: "a todo of your own"}, nil
	}
	if tell == nil {
		return 0, nil, fmt.Errorf("task.new needs the supervisor to route its ask")
	}
	asked, err = tell.TaskAsk(tx, AskReq{From: me, To: owner, Body: body, Kind: kind, NoCard: true, Notify: true, Within: open})
	if err != nil {
		return 0, nil, err
	}
	return tid, asked, nil
}
