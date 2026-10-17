package chatwrites

import (
	"database/sql"
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/attachments"
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/textsafe"
)

// say, ask, push, push.answer, push.cancelled and progress.get (handline/daemon/rpc_messages.py, rpc_taskverbs.py): an agent's messages and the
// nudges typed after them. The message and the daemon's record of it are one transaction; the typing follows the commit.

func (w *writes) registerMessages(s *rpc.Server) {
	s.Register("say", rpc.Method{Role: "agent", Write: true, Handler: w.say})
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
	paths, err := attachments.FilePaths(p)
	if err != nil {
		return nil, err
	}
	me, speaker := sender(c)
	body = textsafe.Text(body)
	var out *supervisor.SayVerbOut
	res, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		if _, err := w.images.CheckMessage(tx, body, "body", nil, c.Caller.Role == "agent"); err != nil {
			return nil, err
		}
		files, err := w.images.AddImages(tx, w.e.Home, me, me == db.Human, paths)
		if err != nil {
			return nil, err
		}
		out, err = w.e.SayVerb(tx, supervisor.SayVerbIn{Requester: me, Speaker: speaker, Target: target, Body: body, Task: task, NoTask: noTask,
			Parent: parent, Effect: func(q db.Q, _ string, rid int64) error { return w.images.Carry(q, rid, files) }})
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
