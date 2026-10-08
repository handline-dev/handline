package chatwrites

import (
	"database/sql"
	"fmt"
	"strconv"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/textsafe"
)

// say, ask, push, push.answer, push.cancelled and progress.get (handline/daemon/rpc_messages.py, rpc_taskverbs.py): an agent's messages and the
// nudges typed after them. The message and the daemon's record of it are one transaction; the typing follows the commit.

func (w *writes) registerMessages(s *rpc.Server) {
	s.Register("say", rpc.Method{Role: "agent", Write: true, Handler: w.say})
	s.Register("ask", rpc.Method{Role: "agent", Write: true, Handler: w.ask})
	s.Register("push", rpc.Method{Role: "agent", Write: true, Handler: w.push})
	s.Register("push.answer", rpc.Method{Role: "agent", Write: true, Handler: w.pushAnswer})
	s.Register("push.cancelled", rpc.Method{Role: "agent", Write: true, Handler: w.pushCancelled})
	s.Register("progress.get", rpc.Method{Role: "agent", Handler: w.progressGet})
}

// numberParam is an optional integer, never a boolean; 0 is none.
func numberParam(p map[string]any, key string) (int64, error) {
	if p[key] == nil {
		return 0, nil
	}
	n, ok := rpc.AsInt(p[key])
	if !ok {
		return 0, badParam(key+" is a number", key)
	}
	return n, nil
}

// sender is who a message is from and who is typing it: an agent is itself; the founder and a device are the person.
func sender(c *rpc.Conn) (me string, speaker *string) {
	me = c.Caller.Actor()
	if c.Caller.Role == "agent" {
		return me, &me
	}
	return me, nil
}

// kept answers a refusal whose writes stand (a denied request is kept as a row).
func kept(msg string) error { return &rpc.CommitThen{Err: rpc.Refused(msg, "", "")} }

func (w *writes) say(c *rpc.Conn, p map[string]any) (any, error) {
	target, err := text(p, "target")
	if err != nil {
		return nil, err
	}
	body, err := text(p, "body")
	if err != nil {
		return nil, err
	}
	task, err := numberParam(p, "task")
	if err != nil {
		return nil, err
	}
	parent, err := numberParam(p, "parent")
	if err != nil {
		return nil, err
	}
	var noTask *string
	if v := p["no_task"]; v != nil {
		s, ok := v.(string)
		if !ok || strings.TrimSpace(s) == "" {
			return nil, badParam("no_task is the reason, as text", "no_task")
		}
		noTask = &s
	}
	me, speaker := sender(c)
	var out *supervisor.SayVerbOut
	res, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		var err error
		out, err = w.e.SayVerb(tx, supervisor.SayVerbIn{Requester: me, Speaker: speaker, Target: target, Body: textsafe.Text(body), Task: task, NoTask: noTask,
			Parent: parent, Effect: func(_ db.Q, _ string, _ int64) error { return nil }})
		if err != nil {
			return nil, err
		}
		if !out.OK {
			return nil, kept(out.Msg)
		}
		return map[string]any{"accepted": true, "said": out.Msg}, nil
	})
	if out != nil && w.e.Wired() {
		out.After()
	}
	if err != nil {
		return nil, err
	}
	return res, nil
}

func (w *writes) ask(c *rpc.Conn, p map[string]any) (any, error) {
	target, err := text(p, "target")
	if err != nil {
		return nil, err
	}
	title, err := text(p, "title")
	if err != nil {
		return nil, err
	}
	body, err := text(p, "body")
	if err != nil {
		return nil, err
	}
	if p["options"] != nil {
		return nil, badParam("options are not served yet: no card stores them", "options")
	}
	me, speaker := sender(c)
	var out *supervisor.AskOut
	res, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		var err error
		out, err = w.e.Ask(tx, supervisor.AskIn{Target: target, Requester: me, Speaker: speaker,
			Body: strings.TrimSpace(textsafe.Oneline(title)) + "\n\n" + strings.TrimSpace(textsafe.Text(body))})
		if err != nil {
			return nil, err
		}
		if !out.OK {
			return nil, kept(out.Message)
		}
		return map[string]any{"accepted": true, "said": out.Message, "request": out.RID}, nil
	})
	if out != nil {
		out.After.Run()
		if err == nil && w.e.Wired() {
			w.e.PushAct(target, out.RID)
		}
	}
	return res, err
}

// nudge records the answer and leaves the typing to act, after the commit: a crash between loses one nudge, which the tick sends anyway, and
// a retry answers without nudging twice.
func (w *writes) nudge(c *rpc.Conn, check func(tx *sql.Tx) error, act func() string) (any, error) {
	if _, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		if err := check(tx); err != nil {
			return nil, err
		}
		return map[string]any{"accepted": true, "said": supervisor.Handed}, nil
	}); err != nil {
		return nil, err
	}
	said := supervisor.Handed
	if w.e.Wired() {
		said = act()
	}
	return map[string]any{"accepted": true, "said": said}, nil
}

func (w *writes) push(c *rpc.Conn, p map[string]any) (any, error) {
	target, err := text(p, "target")
	if err != nil {
		return nil, err
	}
	rid, err := numberParam(p, "request")
	if err != nil {
		return nil, err
	}
	if why := w.e.PushCheck(target); why != "" {
		return nil, rpc.Refused(why, "", "")
	}
	me := c.Caller.Actor()
	return w.nudge(c, func(tx *sql.Tx) error {
		if rid == 0 && p["request"] == nil {
			return nil
		}
		row, err := db.GetRequest(tx, rid)
		if err != nil {
			return err
		}
		if row == nil {
			return notFound("request", strconv.FormatInt(rid, 10), fmt.Sprintf("no request #%d", rid))
		}
		if row.Requester != me {
			return rpc.Refused(fmt.Sprintf("#%d is not yours to push", rid), "state", "")
		}
		return nil
	}, func() string { return w.e.PushAct(target, rid) })
}

func (w *writes) pushAnswer(c *rpc.Conn, p map[string]any) (any, error) {
	requester, err := text(p, "requester")
	if err != nil {
		return nil, err
	}
	if why := w.e.PushAnswerCheck(requester); why != "" {
		return nil, rpc.Refused(why, "", "")
	}
	return w.nudge(c, func(*sql.Tx) error { return nil }, func() string { return w.e.PushAnswerAct(requester) })
}

func (w *writes) pushCancelled(c *rpc.Conn, p map[string]any) (any, error) {
	if p["request"] == nil {
		return nil, badParam("request is a request number", "request")
	}
	rid, err := numberParam(p, "request")
	if err != nil {
		return nil, err
	}
	var row *db.Request
	return w.nudge(c, func(tx *sql.Tx) error {
		got, err := db.GetRequest(tx, rid)
		if err != nil {
			return err
		}
		if got == nil {
			return notFound("request", strconv.FormatInt(rid, 10), fmt.Sprintf("no request #%d", rid))
		}
		var why string
		if row, why, err = w.e.NotifyCancelledCheck(tx, rid); err != nil {
			return err
		} else if why != "" {
			return rpc.Refused(why, "", "")
		}
		return nil
	}, func() string { return w.e.NotifyCancelledAct(row) })
}

// progressGet is the note kept on the agent's effective head, or on its current task, else on the agent, when it has no request in progress.
func (w *writes) progressGet(c *rpc.Conn, p map[string]any) (any, error) {
	if c.Caller.Role != "agent" {
		return nil, rpc.Refused("a progress note is an agent's own: only an agent has one", "", "")
	}
	me := c.Caller.Actor()
	head, err := w.e.EffectiveHead(w.e.DB, me)
	if err != nil {
		return nil, err
	}
	key := ""
	if head == nil || !head.DeliveredAt.Valid {
		tid, set, err := db.CurrentTask(w.e.DB, me)
		if err != nil {
			return nil, err
		}
		key = db.AgentProgressKey(me)
		if set {
			key = fmt.Sprintf("progress-task:%d", tid)
		}
	} else {
		key = fmt.Sprintf("progress:%d", head.ID)
	}
	note, _, err := db.Meta(w.e.DB, key)
	if err != nil {
		return nil, err
	}
	var out any
	if note != "" {
		out = note
	}
	return map[string]any{"note": out}, nil
}
