package supervisor

import (
	"database/sql"
	"errors"
	"fmt"
	"os"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/team"
)

// Delivering queued requests (daemon/delivery.py): checking the routing edge, picking the head, telling the agent.

// OverrideKinds are the kinds ask accepts for a task or a say regardless of what the routing edge carries: enforce_edges
// must not treat that override as a changed edge.
var OverrideKinds = map[string]bool{"task": true, "say": true}

const sayKind = "say"

func strOf(p *string) string {
	if p == nil {
		return ""
	}
	return *p
}

// sameText is a nullable column against a decision's optional value (None equals None).
func sameText(col sql.NullString, v *string) bool {
	if !col.Valid || v == nil {
		return !col.Valid && v == nil
	}
	return col.String == *v
}

// RepliesTo is ask.replies_to: does a request from requester to target answer parent, one target sent to requester.
func RepliesTo(parent *db.Request, requester, target string) bool {
	return parent != nil && parent.Target == requester && parent.Requester == target
}

// EnforceEdges checks every request still waiting for the agent against the founder's routing table. A request whose edge the table does
// not allow is denied before anyone is told of it. False when the table cannot be trusted (changed, unsealed or unreadable): nothing is
// delivered until the founder has looked. A request from a name that is not a registered agent (the system's own) is not an
// agent's edge.
func (e *Env) EnforceEdges(q db.Q, agent string) (ok bool, err error) {
	defer Catch(&err)
	hold := "edgehold:" + agent
	t, lerr := team.LoadTeam(e.Home)
	if lerr != nil {
		switch {
		case errors.Is(lerr, os.ErrNotExist):
			for _, r := range must(db.QueuedFor(q, agent)) {
				check(db.ClearEdge(q, r.ID))
			}
			return true, nil
		default:
			if must(db.Cursor(q, hold)) == 0 {
				check(db.SetCursor(q, hold, 1))
				e.log("  %s: nothing delivered - team.yaml is not trusted (%v)", agent, lerr)
			}
			return false, nil
		}
	}
	if must(db.Cursor(q, hold)) != 0 {
		check(db.SetCursor(q, hold, 0))
	}
	for _, r := range must(db.QueuedFor(q, agent)) {
		if r.EdgeOK.Int64 != 0 {
			continue
		}
		if _, known := t.Agents[r.Requester]; !known {
			check(db.ClearEdge(q, r.ID))
			continue
		}
		var parent *db.Request
		if r.ParentID.Valid {
			parent = must(db.GetRequest(q, r.ParentID.Int64))
		}
		d := t.Route(r.Requester, r.Target, int(r.ChainDepth), RepliesTo(parent, r.Requester, r.Target))
		kind := d.Kind
		if r.Kind.Valid && OverrideKinds[r.Kind.String] {
			k := r.Kind.String
			kind = &k
		}
		var why string
		switch {
		case !d.Allowed:
			why = d.Reason
		case !sameText(r.Relation, d.Relation) || !sameText(r.Response, &d.Response) || !sameText(r.Kind, kind):
			why = fmt.Sprintf("the routing table says %s/%s/%s, the request says %s/%s/%s",
				pyNone(d.Relation), d.Response, pyNone(kind), pyNullNone(r.Relation), pyNullNone(r.Response), pyNullNone(r.Kind))
		default:
			check(db.ClearEdge(q, r.ID))
			continue
		}
		must(db.RetireQueued(q, r.ID, "denied", "refused at delivery: "+why))
		e.log("  %s: #%d from %s refused - %s", agent, r.ID, r.Requester, why)
	}
	return true, nil
}

func pyNone(p *string) string {
	if p == nil {
		return "None"
	}
	return *p
}

func pyNullNone(ns sql.NullString) string {
	if !ns.Valid {
		return "None"
	}
	return ns.String
}

// HeadTaskBlocked is delivery.head_task_blocked: is the head's own task on record as waited on, blocked by another open task or
// held up on an ask the agent itself sent that is linked to the same task. A head with no task is never this.
func (e *Env) headTaskBlocked(q db.Q, agent string, head *db.Request) bool {
	return must(db.HeadTaskBlocked(q, agent, head))
}

// EffectiveHead is db.EffectiveHead: db.head_of_queue unless it is blocked on record, then the next request behind it.
func (e *Env) effectiveHead(q db.Q, agent string) *db.Request {
	return must(db.EffectiveHead(q, agent))
}

// EffectiveHeadOrMarked is EffectiveHead, or with none the oldest send_now_pending row (a say is never a head, but is still the one thing waiting).
func (e *Env) effectiveHeadOrMarked(q db.Q, agent string) *db.Request {
	if h := e.effectiveHead(q, agent); h != nil {
		return h
	}
	if rows := must(db.SendNowPending(q, agent)); len(rows) > 0 {
		return rows[0]
	}
	return nil
}

// EffectiveTaskHead is db.task_head unless it is blocked on record: then the next task behind it.
func (e *Env) effectiveTaskHead(q db.Q, agent string) *db.Request {
	head := must(db.TaskHead(q, agent, 0))
	if head != nil && e.headTaskBlocked(q, agent, head) {
		return must(db.TaskHead(q, agent, head.ID))
	}
	return head
}

// held says once per reason why head is not typed at the agent yet; always false, the delivery's answer.
func (e *Env) holdBack(agent string, head *db.Request, why string, contestable bool) bool {
	e.NoteHold(agent, why, contestable)
	e.mu.Lock()
	same := e.held[agent] == why
	e.held[agent] = why
	e.mu.Unlock()
	if !same {
		e.log("  %s: #%d is held - %s", agent, head.ID, why)
	}
	return false
}

func (e *Env) forgetHeld(agent string) {
	e.init()
	e.mu.Lock()
	delete(e.held, agent)
	e.mu.Unlock()
	e.NoteHold(agent, "", false)
}

// untoldBehind is the tasks behind head that nobody has told the agent about and that are not themselves blocked on record, in queue order.
func (e *Env) untoldBehind(q db.Q, agent string, head *db.Request) []*db.Request {
	var out []*db.Request
	for _, r := range must(db.QueuedFor(q, agent)) {
		if r.Kind.String == "task" && r.EdgeOK.Int64 != 0 && r.ID != head.ID && !r.DeliveredAt.Valid && !e.headTaskBlocked(q, agent, r) {
			out = append(out, r)
		}
	}
	return out
}

// BatchEveryS: what is queued for an agent goes out together, once a minute, not one task at a time. When the last batch went out is
// batch:<agent> in the db, so a restart neither sends early nor forgets what is still owed.
const BatchEveryS = 60

func urgent(r *db.Request) bool { return r.Requester == db.Human || r.TellsNow() }

func (e *Env) batchDue(q db.Q, agent string, rows []*db.Request, freshSession bool) bool {
	if freshSession {
		return true // a session that knows nothing of its queue (new, or voided): not a batch
	}
	for _, r := range rows {
		if urgent(r) {
			return true
		}
	}
	return e.now()-must(db.Cursor(q, "batch:"+agent)) >= BatchEveryS
}

// DeliverQueued tells an agent about the tasks queued for it, only tasks: a message is typed at once (TellNow). Everything not yet told
// goes out as one message once a minute, or at once when it holds the founder's request or one marked send-now; only one task is the
// agent's to do at a time, the rest are told so it knows what is queued. Whatever holds the batch back is logged, once per reason.
func (e *Env) DeliverQueued(q db.Q, agent string) (sent bool, err error) {
	defer Catch(&err)
	if ok, err := e.EnforceEdges(q, agent); !ok || err != nil {
		return false, err
	}
	head := e.effectiveTaskHead(q, agent)
	if head == nil {
		e.forgetHeld(agent)
		return false, nil
	}
	changed := e.sessionChanged(q, agent)
	headDue := must(db.Cursor(q, "notify:"+agent)) != head.ID || changed
	if headDue && must(db.ToldAhead(q, agent, head.ID)) && !changed {
		// Told already, out of turn, to the session that is running; becoming the head is not news.
		check(db.SetCursor(q, "notify:"+agent, head.ID))
		headDue = false
	}
	var aheadOf *db.Request
	var batch []*db.Request
	if headDue {
		batch = append([]*db.Request{head}, e.untoldBehind(q, agent, head)...)
	} else {
		// The head was told and is still open: what is behind it is told as a batch.
		aheadOf = head
		batch = e.untoldBehind(q, agent, head)
		if len(batch) == 0 {
			e.forgetHeld(agent)
			return false, nil
		}
		head = batch[0]
	}
	fresh := headDue && (e.sessionChanged(q, agent) || must(db.Cursor(q, "notify:"+agent)) == 0)
	if !e.batchDue(q, agent, batch, fresh) {
		return false, nil // collected; goes out when the minute is up
	}
	// Only a structured or peer session takes a write; nothing is typed at a terminal.
	if !e.structuredAgent(agent) || !e.Agents.Live(agent) {
		why := "no live session"
		if !e.structuredAgent(agent) && e.openSession(agent, stateOf(q, agent)) {
			why = "peer_inbox is off in agent.yaml; nothing types any more" // a terminal is up, but nothing here reaches it
		}
		return e.holdBack(agent, head, why, false), nil
	}
	depth := 0
	for _, r := range must(db.QueuedFor(q, agent)) {
		if r.Kind.String == "task" {
			depth++
		}
	}
	if sent = e.tellBatch(q, agent, batch, depth, aheadOf, aheadOf != nil); sent {
		check(db.SetCursor(q, "batch:"+agent, e.now()))
		e.forgetHeld(agent)
	}
	return sent, nil
}
