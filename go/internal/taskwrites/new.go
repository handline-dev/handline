package taskwrites

import (
	"context"
	"database/sql"
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/taskresult"
	"github.com/handline/handline/go/internal/textsafe"
	"github.com/handline/handline/go/internal/timefmt"
	"github.com/handline/handline/go/internal/watch"
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

// optionalBool is a true or false param, false when absent.
func optionalBool(p map[string]any, key string) (bool, error) {
	if p[key] == nil {
		return false, nil
	}
	b, ok := p[key].(bool)
	if !ok {
		return false, badParam(key+" is true or false", key)
	}
	return b, nil
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
			body = strings.TrimSpace(textsafe.Text(b))
		}
		secs, err := timeBox(p, "time_box")
		if err != nil {
			return nil, err
		}
		if owner != human && owner != db.Gate && !contains(paths.ListAgents(home), owner) {
			return nil, agentNotFound(owner)
		}
		watched, err := watchParam(p, owner)
		if err != nil {
			return nil, err
		}
		if watched != nil && secs == 0 {
			secs = db.GateDeadlineS
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
		points, _, err := estimateParam(p)
		if err != nil {
			return nil, err
		}
		ui, err := optionalBool(p, "ui")
		if err != nil {
			return nil, err
		}
		duplicateOK := false
		if v := p["duplicate_ok"]; v != nil {
			b, isBool := v.(bool)
			if !isBool {
				return nil, badParam("duplicate_ok is true or false", "duplicate_ok")
			}
			duplicateOK = b
		}
		parent, hasParent, err := optionalTask(p, "parent")
		if err != nil {
			return nil, err
		}
		noParent, err := optionalBool(p, "no_parent")
		if err != nil {
			return nil, err
		}
		if noParent && hasParent {
			return nil, badParam("no_parent and parent together: choose one", "no_parent")
		}
		tags, err := TagList(p, "tags")
		if err != nil {
			return nil, err
		}
		if len(tags) > maxTags {
			return nil, badParam(fmt.Sprintf("a task carries at most %d tags", maxTags), "tags")
		}
		blocker, hasBlocker, err := optionalTask(p, "blocked_by")
		if err != nil {
			return nil, err
		}
		me := c.Caller.Actor()
		expects := "report"
		if owner == human && me != human { // a task given to the founder is a decision
			expects = "decision"
		}
		clean := strings.TrimSpace(textsafe.Oneline(title))
		var (
			tid   int64
			asked *Asked
			freed []string
		)
		out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			freed = nil
			if err := claimImages(tx, home, me, body, "", "body"); err != nil {
				return nil, err
			}
			var err error
			if !hasParent && !noParent && owner != db.Gate {
				if parent, err = requestHeld(tx, me); err != nil {
					return nil, err
				}
				hasParent = parent != 0
			}
			tid, asked, err = OpenTask(tx, tell, me, NewIn{Owner: owner, Title: clean, Body: body, Secs: secs, Prio: prio, Estimate: points, Expects: expects,
				Parent: parent, HasParent: hasParent, Blocker: blocker, HasBlocker: hasBlocker, Reporter: reporter, UI: ui, DuplicateOK: duplicateOK, Tags: tags,
				Watch: watched})
			if err != nil {
				return nil, err
			}
			if !asked.OK {
				return nil, &rpc.CommitThen{Err: refusal(asked.Msg)}
			}
			// The new task blocks its parent, and its own blocker may block it.
			for _, blocked := range []int64{parent, tid} {
				if o, err := freeHolder(tx, blocked); err != nil {
					return nil, err
				} else if o != "" {
					freed = append(freed, o)
				}
			}
			return accepted(fmt.Sprintf("task T%d opened for %s", tid, owner), map[string]any{"task": tid}), nil
		})
		for _, o := range freed {
			if err == nil && tell != nil {
				tell.Edited(Edited{Task: tid, By: me, Freed: o})
			}
		}
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
		out["said"] = fmt.Sprintf("task T%d opened for %s (P%d%s): %s", tid, owner, prio, limit, msg)
		return out, nil
	}
}

// requestHeld is the request task an agent is working on, which a task it opens hangs under unless it says otherwise: its current task, when
// that is the root of a request (made from a founder's message). 0 for none.
func requestHeld(tx *sql.Tx, agent string) (int64, error) {
	if agent == human {
		return 0, nil
	}
	tid, held, err := db.CurrentTask(tx, agent)
	if err != nil || !held {
		return 0, err
	}
	var n int
	err = tx.QueryRow("SELECT COUNT(*) FROM tasks WHERE id = ? AND from_request IS NOT NULL AND done_evidence IS NULL AND discarded_by IS NULL", tid).Scan(&n)
	if n == 0 {
		return 0, err
	}
	return tid, err
}

// NewIn is what OpenTask opens: the fields task.new has checked. Owner is an agent or the founder; Expects and Prio are set.
type NewIn struct {
	Owner, Title, Body string
	Reporter           string // who the task answers to; "" is the opener
	Tags               []string
	Secs, Prio         int64
	Estimate           int64 // story points, 0 for none
	Expects            string
	Parent, Blocker    int64
	HasParent          bool
	HasBlocker         bool
	UI                 bool      // the task shows something on a screen (a label; done never requires a screenshot)
	DuplicateOK        bool      // open it even when an open task under the parent has nearly its title
	Watch              *db.Watch // what a gate (Owner db.Gate) watches; nil for any other owner
}

// watchParam is task.new's watch {vendor, target, until?}: required with owner gate, refused with any other.
func watchParam(p map[string]any, owner string) (*db.Watch, error) {
	raw, given := p["watch"]
	switch {
	case owner == db.Gate && !given:
		return nil, badParam("a gate watches something: watch {vendor, target, until}", "watch")
	case owner != db.Gate && given:
		return nil, badParam("watch goes with owner gate", "watch")
	case !given:
		return nil, nil
	}
	w, err := parseWatch(raw)
	if err != nil {
		return nil, err
	}
	return w, watchReady(w)
}

// parseWatch is a watch param made whole by its vendor without reading anything (watch.Parse); every refusal is a bad watch.
func parseWatch(raw any) (*db.Watch, error) {
	m, ok := raw.(map[string]any)
	if !ok {
		return nil, badParam("watch is {vendor, target, until}", "watch")
	}
	vendor, _ := m["vendor"].(string)
	target, _ := m["target"].(string)
	until, isText := m["until"].(string)
	if m["until"] != nil && !isText {
		return nil, badParam("watch.until is text", "watch")
	}
	w, err := watch.Parse(vendor, target, until)
	if err != nil {
		return nil, badParam(err.Error(), "watch")
	}
	return &w, nil
}

// watchReady refuses a watch whose vendor is not set up (watch.Ready), which may read the outside.
func watchReady(w *db.Watch) error {
	if err := watch.Ready(context.Background(), *w); err != nil {
		return refusal(err.Error())
	}
	return nil
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
		if hasParent { // the parent waits on the new task, so a blocker that waits on the parent would close a loop
			loop, err := cyclePath(tx, parent, blocker)
			if err != nil {
				return 0, nil, err
			}
			if blocker == parent || loop != nil {
				ring := []string{"the new task"}
				for _, n := range loop {
					ring = append(ring, fmt.Sprintf("T%d", n))
				}
				ring = append(ring, fmt.Sprintf("T%d", parent), "the new task")
				return 0, nil, refusal(fmt.Sprintf("a child of T%d cannot wait on T%d: that would be a cycle, %s", parent, blocker, strings.Join(ring, " -> ")))
			}
		}
	}
	if hasParent {
		if ok, err := hasTask(tx, parent); err != nil {
			return 0, nil, err
		} else if !ok {
			return 0, nil, refusal(fmt.Sprintf("no task T%d", parent))
		}
		if !in.DuplicateOK {
			if err := duplicateRefusal(tx, parent, clean); err != nil {
				return 0, nil, err
			}
		}
	}
	open := func(rid int64) error {
		var err error
		reporter := in.Reporter
		if reporter == me {
			reporter = ""
		}
		if tid, err = db.NewTask(tx, clean, owner, me, parent, prio, expects, secs, reporter); err != nil {
			return err
		}
		if in.Estimate != 0 {
			if _, err := tx.Exec("UPDATE tasks SET estimate = ? WHERE id = ?", in.Estimate, tid); err != nil {
				return err
			}
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
		if rid != 0 {
			if err := db.LinkRequest(tx, tid, rid); err != nil {
				return err
			}
		}
		if _, held, err := db.CurrentTask(tx, owner); err != nil {
			return err
		} else if owner != human && owner != db.Gate && !held {
			if err := db.SetCurrentTask(tx, owner, tid, true); err != nil {
				return err
			}
		}
		payload := []kv{{"task_id", tid}, {"request_id", rid}, {"owner", owner}, {"expects", expects}}
		if len(in.Tags) > 0 {
			payload = append(payload, kv{"tags", in.Tags})
		}
		for _, tag := range in.Tags {
			if _, err := tx.Exec("INSERT INTO task_tags(task_id, tag) VALUES(?, ?)", tid, tag); err != nil {
				return err
			}
		}
		if err := addEvent(tx, me, "task_new", payload...); err != nil {
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
		if hasBlocker {
			if why, err := recordBlock(tx, tid, blocker, me); err != nil {
				return err
			} else if why != "" {
				return refusal(why)
			}
		}
		return nil
	}
	if in.Watch != nil {
		// A gate asks no one: the daemon holds it and closes it when what it watches changes.
		if err := open(0); err != nil {
			return 0, nil, err
		}
		if err := db.OpenGate(tx, tid, me, *in.Watch); err != nil {
			return 0, nil, err
		}
		return tid, &Asked{OK: true, Msg: "watching " + in.Watch.Label}, nil
	}
	if me == human && owner == human {
		// A todo of the founder's own: routing has no founder-to-founder edge, so its brief is written as the request any task to the
		// founder carries, queued on their screen, without being routed or told.
		rid, err := db.AddRequest(tx, db.NewRequest{Requester: human, Target: human, Body: body, Kind: "task"})
		if err != nil {
			return 0, nil, err
		}
		if err := open(rid); err != nil {
			return 0, nil, err
		}
		return tid, &Asked{OK: true, Msg: fmt.Sprintf("a todo of your own, %s", db.MsgName(tx, rid)), RID: rid}, nil
	}
	if tell == nil {
		return 0, nil, fmt.Errorf("task.new needs the supervisor to route its ask")
	}
	asked, err = tell.TaskAsk(tx, AskReq{From: me, To: owner, Body: body, Kind: "task", NoCard: true, Notify: true, Within: open})
	if err != nil {
		return 0, nil, err
	}
	return tid, asked, nil
}
