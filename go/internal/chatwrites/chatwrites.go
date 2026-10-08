// Package chatwrites serves the chat page's write methods (handline/daemon/rpc_chatwrites.py, rpc_chat_now.py) and the actions behind them
// (handline/actions/requests.py): each writes its effect and its write_effects record in one transaction (rpc.Conn.Effect), so a retry after
// a crash answers and repeats nothing. The telling follows the commit, on the supervisor's Env.
package chatwrites

import (
	"database/sql"
	"fmt"
	"strconv"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/taskwrites"
	"github.com/handline/handline/go/internal/textsafe"
	"github.com/handline/handline/go/internal/timefmt"
)

// maxTaskTimeoutS is actions.MAX_TASK_TIMEOUT_S: the longest time box a task is given.
const maxTaskTimeoutS = 4 * 3600

// Register adds the chat writes to the server's table.
// tell is what follows a task the writes open or settle once it has committed.
func Register(s *rpc.Server, e *supervisor.Env, tell taskwrites.Teller) {
	w := &writes{e, tell}
	s.Register("chat.send", rpc.Method{Role: "member", Write: true, Handler: w.send, Refresh: w.refreshSend})
	s.Register("chat.send_now", rpc.Method{Role: "member", Write: true, Handler: w.sendNow})
	s.Register("chat.answer", rpc.Method{Role: "member", Write: true, Handler: w.answer})
	s.Register("chat.dismiss", rpc.Method{Role: "member", Write: true, Handler: w.dismiss})
	s.Register("chat.cancel", rpc.Method{Role: "member", Write: true, Handler: w.cancel})
	s.Register("chat.seen", rpc.Method{Role: "member", Write: true, Handler: w.seen})
	s.Register("inbox.mark_read", rpc.Method{Role: "agent", Write: true, Handler: w.markRead})
	s.Register("inbox.answer", rpc.Method{Role: "member", Write: true, Handler: w.inboxAnswer})
	s.Register("inbox.reply", rpc.Method{Role: "agent", Write: true, Handler: w.inboxReply})
	w.registerMessages(s)
	w.registerAnswers(s)
}

type writes struct {
	e    *supervisor.Env
	tell taskwrites.Teller
}

func badParam(message, field string) *rpc.Error {
	return rpc.BadRequestError(message, field, rpc.InvalidParams)
}

// requestParam is a message number: an integer, never a boolean.
func requestParam(p map[string]any, key string) (int64, error) {
	n, ok := rpc.AsInt(p[key])
	if !ok {
		return 0, badParam(key+" is a message number", key)
	}
	return n, nil
}

func text(p map[string]any, key string) (string, error) {
	s, ok := p[key].(string)
	if !ok || strings.TrimSpace(s) == "" {
		return "", badParam(key+" is text and not empty", key)
	}
	return s, nil
}

func notFound(what, name, message string) *rpc.Error {
	e := rpc.NotFoundError(message)
	e.Data = map[string]any{"what": what, "name": name}
	return e
}

func message(q db.Q, rid int64) (*db.Request, error) {
	row, err := db.GetRequest(q, rid)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, notFound("message", strconv.FormatInt(rid, 10), fmt.Sprintf("no message %d", rid))
	}
	return row, nil
}

func (w *writes) agentNamed(p map[string]any) (string, error) {
	name, ok := p["agent"].(string)
	if !ok || name == "" {
		return "", badParam("agent is an agent's name", "agent")
	}
	for _, a := range paths.ListAgents(w.e.Home) {
		if a == name {
			return name, nil
		}
	}
	return "", notFound("agent", name, "no such agent: "+name)
}

// refuse is an action's "no": its writes stand when it says so (a denied request is kept as a row), else nothing is written.
func refuse(r *Result) error {
	err := rpc.Refused(r.Msg, "", "")
	if r.Keep {
		return &rpc.CommitThen{Err: err}
	}
	return err
}

// stateRefusal is refuse with the gate "state": the message is not in a condition the call can act on.
func stateRefusal(r *Result) error {
	err := rpc.Refused(r.Msg, "state", "")
	if r.Keep {
		return &rpc.CommitThen{Err: err}
	}
	return err
}

// writeResult runs fn in the call's transaction and replies with what the action told once it committed. fn answers the action's result and the
// extra members the reply carries; a refusal is turned into the error that word is, by refusal.
func (w *writes) writeResult(c *rpc.Conn, refusal func(*Result) error, fn func(tx *sql.Tx) (*Result, map[string]any, error)) (any, error) {
	var res *Result
	_, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		var more map[string]any
		var err error
		if res, more, err = fn(tx); err != nil {
			return nil, err
		}
		if !res.OK {
			return nil, refusal(res)
		}
		out := map[string]any{"accepted": true, "said": res.Note}
		for k, v := range more {
			out[k] = v
		}
		return out, nil
	})
	if res != nil && !res.OK && res.Keep && res.Tell != nil {
		res.Tell() // the refusal's writes stood: what follows them still runs
	}
	if err != nil {
		return nil, err
	}
	out := map[string]any{"accepted": true, "said": res.Tell()}
	if res.Request != 0 {
		out["request"] = res.Request
	}
	return out, nil
}

func (w *writes) send(c *rpc.Conn, p map[string]any) (any, error) {
	body, err := text(p, "body")
	if err != nil {
		return nil, err
	}
	agent, _ := p["agent"].(string)
	if agent == "" {
		return nil, badParam("agent is an agent's name", "agent")
	}
	d, err := deliveryParam(p)
	if err != nil {
		return nil, err
	}
	task := p["task"]
	var parent int64
	if v := p["parent"]; v != nil {
		if task != nil {
			return nil, badParam("a message under another is not a task", "parent")
		}
		if parent, err = requestParam(p, "parent"); err != nil {
			return nil, err
		}
	}
	if task != nil {
		if err := checkTask(task); err != nil {
			return nil, err
		}
	}
	if _, err := w.agentNamed(p); err != nil {
		return nil, err
	}
	if task != nil {
		return w.sendTask(c, agent, body, task)
	}
	body = strings.TrimSpace(textsafe.Text(body))
	var act func(rid int64)
	var receipt map[string]any
	if w.e.StructuredAgent(agent) {
		act = w.sendAct(agent, d, &receipt)
	}
	out, err := w.writeResult(c, refuse, func(tx *sql.Tx) (*Result, map[string]any, error) {
		var res *Result
		var err error
		if parent != 0 {
			res, err = w.sendUnder(tx, agent, parent, body, act)
		} else {
			res, err = sayWrite(w.e, tx, db.Human, agent, body, 0, nil, act)
		}
		if res != nil {
			return res, map[string]any{"request": res.Request}, err
		}
		return nil, nil, err
	})
	if m, ok := out.(map[string]any); ok && receipt != nil {
		m["receipt"] = receipt
	}
	return out, err
}

// checkTask is the shape of send's task: {title, time_box}.
func checkTask(task any) error {
	t, ok := task.(map[string]any)
	if !ok {
		return badParam("task is {title, time_box}", "task")
	}
	if _, err := text(t, "title"); err != nil {
		return err
	}
	if t["time_box"] == nil {
		return nil
	}
	s, err := text(t, "time_box")
	if err != nil {
		return err
	}
	secs, err := timefmt.ParseSpan(s)
	if err == nil && secs > maxTaskTimeoutS {
		err = fmt.Errorf("a task timeout is at most %s (%s is longer)", timefmt.Span(maxTaskTimeoutS, false), s)
	}
	if err != nil {
		return badParam(err.Error(), "time_box")
	}
	return nil
}

// sendUnder is a message that replies to parent: its answer when it waits on the person, else a say to the agent under it.
func (w *writes) sendUnder(tx *sql.Tx, agent string, parent int64, body string, act func(rid int64)) (*Result, error) {
	row, err := message(tx, parent)
	if err != nil {
		return nil, err
	}
	if agent != row.Target && agent != row.Requester {
		return nil, rpc.Refused(fmt.Sprintf("%s is not on #%d", agent, parent), "state", "")
	}
	if !WaitsOnPerson(row) {
		return sayWrite(w.e, tx, db.Human, agent, body, parent, nil, act)
	}
	res, err := AnswerWrite(w.e, w.tell, tx, parent, body, nil)
	if err != nil {
		return nil, err
	}
	if !res.OK {
		return nil, stateRefusal(res)
	}
	res.Request = parent
	return res, nil
}

func (w *writes) answer(c *rpc.Conn, p map[string]any) (any, error) {
	rid, err := requestParam(p, "request")
	if err != nil {
		return nil, err
	}
	body, err := text(p, "text")
	if err != nil {
		return nil, err
	}
	return w.writeResult(c, refuse, func(tx *sql.Tx) (*Result, map[string]any, error) {
		row, err := message(tx, rid)
		if err != nil {
			return nil, nil, err
		}
		if !WaitsOnPerson(row) {
			return nil, nil, rpc.Refused(fmt.Sprintf("#%d asked nothing of the person: there is nothing to answer", rid), "state", "")
		}
		res, err := AnswerWrite(w.e, w.tell, tx, rid, body, nil)
		return res, nil, err
	})
}

func (w *writes) dismiss(c *rpc.Conn, p map[string]any) (any, error) {
	rid, err := requestParam(p, "request")
	if err != nil {
		return nil, err
	}
	return w.writeResult(c, refuse, func(tx *sql.Tx) (*Result, map[string]any, error) {
		row, err := message(tx, rid)
		if err != nil {
			return nil, nil, err
		}
		if row.Target != db.Human && row.Requester != db.Human {
			return nil, nil, rpc.Refused(fmt.Sprintf("#%d is not between the person and an agent", rid), "state", "")
		}
		res, err := DismissWrite(w.e, tx, rid, c.Caller.Actor(), "", nil, nil, nil)
		return res, nil, err
	})
}

func (w *writes) cancel(c *rpc.Conn, p map[string]any) (any, error) {
	rid, err := requestParam(p, "request")
	if err != nil {
		return nil, err
	}
	confirm := false
	if v, ok := p["confirm"]; ok && v != nil {
		if confirm, ok = v.(bool); !ok {
			return nil, badParam("confirm is true or false", "confirm")
		}
	}
	needsConfirm := func(plan string) error {
		e := rpc.Refused(plan, "confirm", "")
		e.Data["plan"] = plan
		return e
	}
	after := &supervisor.After{}
	if err := w.withdrawHeld(rid); err != nil {
		return nil, err
	}
	res, err := w.writeResult(c, refuse, func(tx *sql.Tx) (*Result, map[string]any, error) {
		row, err := message(tx, rid)
		if err != nil {
			return nil, nil, err
		}
		if row.Requester != db.Human {
			return nil, nil, rpc.Refused(fmt.Sprintf("#%d is not a message the person sent", rid), "state", "")
		}
		ok, plan, err := CancelPlan(tx, rid)
		if err != nil {
			return nil, nil, err
		}
		if !ok {
			return nil, nil, rpc.Refused(plan, "state", "")
		}
		if row.DeliveredAt.Valid && !confirm {
			return nil, nil, needsConfirm(plan)
		}
		// a delivery between the check above and this write needs the confirm too
		var late string
		guard := func(q db.Q, now *db.Request) string {
			if now.DeliveredAt.Valid && !confirm {
				_, late, _ = CancelPlan(q, rid)
				return late
			}
			return ""
		}
		var note string
		cp, ok, msg, err := w.e.CancelWrite(tx, after, rid, func(_ db.Q, m string) error { note = m; return nil }, guard)
		if err != nil {
			return nil, nil, err
		}
		if late != "" {
			return nil, nil, needsConfirm(late)
		}
		if !ok {
			return refusal(msg), nil, nil
		}
		return &Result{OK: true, Note: note, Tell: func() string {
			if cp == nil { // never delivered: withdrawn, and nobody is told
				return note
			}
			return guarded(w.e, fmt.Sprintf("telling after the cancel of #%d", rid), func() (string, error) { return w.e.CancelTell(w.e.DB, cp) })
		}}, nil, nil
	})
	if err == nil {
		after.Run()
	}
	return res, err
}

func (w *writes) seen(c *rpc.Conn, p map[string]any) (any, error) {
	upto, err := requestParam(p, "up_to")
	if err != nil {
		return nil, err
	}
	agent, err := w.agentNamed(p)
	if err != nil {
		return nil, err
	}
	said := fmt.Sprintf("%s's messages up to #%d are seen", agent, upto)
	return c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		if err := ForYouSeen(tx, agent, upto); err != nil {
			return nil, err
		}
		return map[string]any{"accepted": true, "said": said}, nil
	})
}

func (w *writes) sendNow(c *rpc.Conn, p map[string]any) (any, error) {
	rid, ok := rpc.AsInt(p["request"])
	if !ok {
		return nil, badParam("request is the integer id of a queued message", "request")
	}
	if out, handled, err := w.promoteHeld(c, rid); handled {
		return out, err
	}
	var target string
	out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		row, err := db.GetRequest(tx, rid)
		if err != nil {
			return nil, err
		}
		if row == nil || row.Requester != db.Human {
			return nil, notFound("request", strconv.FormatInt(rid, 10), fmt.Sprintf("no message %d of yours", rid))
		}
		marked, err := db.MarkSendNow(tx, rid)
		if err != nil {
			return nil, err
		}
		if !marked {
			return nil, rpc.Refused(fmt.Sprintf("message %d is no longer waiting: it was sent or closed", rid), "", "")
		}
		target = row.Target
		return map[string]any{"accepted": true, "said": fmt.Sprintf("message %d is told to %s now", rid, target)}, nil
	})
	if err != nil {
		return nil, err
	}
	if w.e.Wired() {
		w.e.PushVerb(target, 0)
	}
	return out, nil
}

// sendTask is chat.send with a task: the message is the task's brief, and the agent is asked and the task row written in one transaction
// (actions.task_new), with the call's record, so a retry opens no second task.
func (w *writes) sendTask(c *rpc.Conn, agent, body string, task any) (any, error) {
	t := task.(map[string]any)
	title, _ := t["title"].(string)
	var secs int64
	if s, ok := t["time_box"].(string); ok {
		secs, _ = timefmt.ParseSpan(s)
	}
	if w.tell == nil {
		return nil, fmt.Errorf("a message that opens a task needs the task teller")
	}
	me := c.Caller.Actor()
	var (
		tid   int64
		asked *taskwrites.Asked
	)
	out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		var err error
		tid, asked, err = taskwrites.OpenTask(tx, w.tell, me, taskwrites.NewIn{Owner: agent, Title: strings.TrimSpace(textsafe.Oneline(title)),
			Body: strings.TrimSpace(textsafe.Text(body)), Secs: secs, Prio: db.DefaultPriority, Expects: "report"})
		if err != nil {
			return nil, err
		}
		if !asked.OK {
			return nil, &rpc.CommitThen{Err: rpc.Refused(asked.Msg, "", "")}
		}
		return map[string]any{"accepted": true, "said": fmt.Sprintf("task T%d opened for %s", tid, agent), "request": asked.RID}, nil
	})
	if asked != nil && !asked.OK && asked.After != nil {
		guarded(w.e, "telling after a refused task", func() (string, error) { return asked.After(), nil }) // the refusal's writes stood
	}
	if err != nil {
		return nil, err
	}
	clean := strings.TrimSpace(textsafe.Oneline(title))
	msg := asked.Msg
	if asked.After != nil {
		if extra := guarded(w.e, fmt.Sprintf("telling after T%d opened", tid), func() (string, error) { return asked.After(), nil }); extra != "" {
			msg += "\n" + extra
		}
	}
	guarded(w.e, fmt.Sprintf("telling the manager after T%d opened", tid), func() (string, error) { w.tell.Opened(tid, me, agent, clean); return "", nil })
	out["said"] = msg
	return out, nil
}
