package legacyverbs

import (
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/pyval"
	"github.com/handline/handline/go/internal/supervisor"
)

// say is `hl say`: the message and its row are one write, kept even when the say is refused as a denied request; the telling follows the
// commit. The sender is the request's own requester, and speaker its caller's word.
func (e *Env) say(p map[string]any) (bool, string, error) {
	target, err := text(p, "target")
	if err != nil {
		return false, "", err
	}
	body, err := text(p, "body")
	if err != nil {
		return false, "", err
	}
	in := supervisor.SayVerbIn{Target: target, Body: body}
	if pyval.Truthy(p["requester"]) {
		in.Requester = pyval.Str(p["requester"])
	}
	if s, ok := p["speaker"].(string); ok {
		in.Speaker = &s
	}
	if in.Task, err = optionalNumber(p, "task"); err != nil {
		return false, "", err
	}
	if in.Parent, err = optionalNumber(p, "parent"); err != nil {
		return false, "", err
	}
	if p["no_task"] != nil {
		s := pyval.Str(p["no_task"])
		in.NoTask = &s
	}
	var out *supervisor.SayVerbOut
	if err := e.within(func(q db.Q) (err error) {
		out, err = e.Sup.SayVerb(q, in)
		return err
	}); err != nil {
		return false, "", err
	}
	if e.Sup.Wired() {
		out.After()
	}
	return out.OK, out.Msg, nil
}

// push tells target about request rid (a message is typed at once, a task keeps the one-at-a-time rule), else its next task.
func (e *Env) push(p map[string]any) (bool, string, error) {
	target, err := text(p, "target")
	if err != nil {
		return false, "", err
	}
	rid, err := optionalNumber(p, "rid")
	if err != nil {
		return false, "", err
	}
	ok, said := e.Sup.PushVerb(target, rid)
	return ok, said, nil
}

// pushAnswer puts an answer in front of the agent that asked.
func (e *Env) pushAnswer(p map[string]any) (bool, string, error) {
	requester, err := text(p, "requester")
	if err != nil {
		return false, "", err
	}
	ok, said := e.Sup.PushAnswerVerb(requester)
	return ok, said, nil
}

// notifyCancelled tells an agent a request it was handed was closed under it; only the verdict on the row is ever typed.
func (e *Env) notifyCancelled(p map[string]any) (bool, string, error) {
	rid, err := number(p, "rid")
	if err != nil {
		return false, "", err
	}
	return e.Sup.NotifyCancelledVerb(e.DB, rid)
}
