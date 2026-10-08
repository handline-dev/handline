package supervisor

import (
	"fmt"
	"strconv"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/structured"
)

// Typing the tell and recording what it left behind (daemon/delivery.py).

// recordTold is what a delivered tell leaves behind, once it is known to have landed.
func (e *Env) recordTold(q db.Q, agent string, head *db.Request, depth int, session string, aheadOf *db.Request, parked bool) {
	more := depth - 1
	typedInto := session // what the request was typed into: the live session when none was given; notified_session takes only what was given
	if typedInto == "" {
		typedInto = e.liveSession(q, agent)
	}
	check(db.MarkTyped(q, head.ID, typedInto))
	e.NoteTyped(head.ID)
	if aheadOf == nil {
		check(db.SetCursor(q, "notify:"+agent, head.ID))
		check(db.SetNotifiedSession(q, agent, session))
	} else {
		check(db.MarkToldAhead(q, agent, head.ID, requestIDs(must(db.QueuedFor(q, agent)))))
	}
	must(db.AddEvent(q, agent, "notified", db.KV{K: "request_id", V: head.ID}, db.KV{K: "more_waiting", V: more}))
	how := ""
	switch {
	case parked:
		how = fmt.Sprintf(" (behind #%d)", aheadOf.ID)
	case aheadOf != nil:
		how = " (send now)"
	}
	if more != 0 {
		how += fmt.Sprintf(" (%d more waiting)", more)
	}
	e.log("  %s: told about #%d%s", agent, head.ID, how)
}

func requestIDs(rs []*db.Request) []int64 {
	out := make([]int64, len(rs))
	for i, r := range rs {
		out[i] = r.ID
	}
	return out
}

// tellBatch types batch (the tasks no one has told the agent about, in queue order) as one message and records each as told. Each is marked
// delivered before the send (it closes withdraw/replace's race) and put back as owed when the send fails. aheadOf is the open head the
// batch is queued behind; parked says so, else it is a "send now" request jumping that head.
func (e *Env) tellBatch(q db.Q, agent string, batch []*db.Request, depth int, handle string, aheadOf *db.Request, parked bool) bool {
	var live []*db.Request
	landed := false
	structuredNow := e.structuredAgent(agent)
	for _, r := range batch {
		if !must(db.MarkDelivered(q, r.ID)) {
			continue // retired in the gap since it was listed
		}
		if !structuredNow && e.landedAlready(q, agent, r.ID) {
			// the transcript first - a line it already holds is not typed again.
			check(db.MarkTyped(q, r.ID, e.liveSession(q, agent)))
			check(db.MarkLanded(q, r.ID))
			e.recordTold(q, agent, r, depth, "", aheadOf, parked)
			landed = true
			continue
		}
		live = append(live, r)
	}
	if len(live) == 0 {
		return landed
	}
	folded := foldedNotices(q, agent)
	var text string
	if len(live) == 1 {
		text = tellText(q, live[0], depth, aheadOf, parked, folded)
	} else {
		text = batchText(q, live, folded, aheadOf)
	}
	confirmed := false
	if structuredNow {
		// Held behind a running turn: a task batch does not interrupt it. The ledger holds the operation, so a retry writes nothing.
		state, _ := e.sendOnce(q, agent, requestOp(requestIDs(live)...), text, structured.Queue)
		if state == NotSent {
			for _, r := range live {
				check(db.ClearDelivered(q, r.ID))
			}
			return false
		}
		confirmed = state == Confirmed
	} else {
		if ok, _ := e.send(handle, text); !ok {
			for _, r := range live {
				check(db.ClearDelivered(q, r.ID))
			}
			return false
		}
		e.markTyped(q, agent, nil)
	}
	defer func() {
		if confirmed {
			for _, r := range live {
				check(db.MarkLanded(q, r.ID))
			}
		}
	}()
	session := e.liveSession(q, agent)
	check(db.MarkNoticesShown(q, requestIDs(folded), session))
	for n, r := range live {
		if n == 0 {
			told := ""
			if aheadOf == nil {
				told = session
			}
			e.recordTold(q, agent, r, depth, told, aheadOf, parked)
			continue
		}
		// Told with the first, to the session that is running: becoming the head is not news.
		check(db.MarkTyped(q, r.ID, session))
		e.NoteTyped(r.ID)
		check(db.MarkToldAhead(q, agent, r.ID, requestIDs(must(db.QueuedFor(q, agent)))))
		must(db.AddEvent(q, agent, "notified", db.KV{K: "request_id", V: r.ID}, db.KV{K: "more_waiting", V: depth - 1}))
		e.log("  %s: told about #%d (batched with #%d)", agent, r.ID, live[0].ID)
	}
	return true
}

// WaitingPrompt is the message waiting for a parked agent, as the first prompt of the session that resumes it, and what to record once that
// session is up (told, called with the session id; q must stay usable until then); ok false when nothing waits. It is the tell DeliverQueued and DeliverAnswers would type
// on the next tick. Marked delivered here, before the launch, for the reason tellBatch is.
func (e *Env) WaitingPrompt(q db.Q, agent string) (text string, told func(session string) error, ok bool, err error) {
	defer Catch(&err)
	if edges, err := e.EnforceEdges(q, agent); !edges || err != nil {
		return "", nil, false, err
	}
	if head := e.effectiveHeadOrMarked(q, agent); head != nil {
		if !must(db.MarkDelivered(q, head.ID)) {
			return "", nil, false, nil
		}
		depth := must(db.QueueDepth(q, agent))
		folded := foldedNotices(q, agent)
		return tellText(q, head, depth, nil, false, folded), func(session string) (err error) {
			defer Catch(&err)
			e.recordTold(q, agent, head, depth, session, nil, false)
			if head.Kind.String == "say" {
				e.FinishSay(q, head)
			}
			check(db.MarkNoticesShown(q, requestIDs(folded), session))
			return nil
		}, true, nil
	}
	if answered := must(db.UnannouncedAnswers(q, agent)); len(answered) > 0 {
		return AnswersText(answered), func(string) (err error) { defer Catch(&err); e.recordAnswers(q, agent, answered); return nil }, true, nil
	}
	// A pending message is the same kind of "owed" as a head or an answer. Typed as-is, not wrapped like a request's tell.
	if pending := must(db.ListPending(q, agent)); len(pending) > 0 {
		row := pending[0]
		return row.Text, func(string) (err error) { defer Catch(&err); return db.DeletePending(q, row.ID) }, true, nil
	}
	return "", nil, false, nil
}

func (e *Env) recordAnswers(q db.Q, agent string, answered []*db.Request) {
	check(db.MarkAnnounced(q, requestIDs(answered)))
	must(db.AddEvent(q, agent, "answered", db.KV{K: "count", V: len(answered)}))
	e.log("  %s: told about %d answer(s)", agent, len(answered))
}

// DeliverAnswers tells an agent what came back from something it asked. Told once, and marked on the row: two answers can come back in
// either order, and a high-water mark then loses the older one for good.
func (e *Env) DeliverAnswers(q db.Q, agent string) (sent bool, err error) {
	defer Catch(&err)
	answered := must(db.UnannouncedAnswers(q, agent))
	if len(answered) == 0 {
		return false, nil
	}
	if e.structuredAgent(agent) {
		// The same answers are the same operation; held behind a running turn.
		state, _ := e.sendOnce(q, agent, "answers:"+idList(requestIDs(answered)), AnswersText(answered), structured.Queue)
		if state == NotSent {
			return false, nil // stays unannounced; told when the session is up
		}
		e.recordAnswers(q, agent, answered)
		return true, nil
	}
	if e.typingHold(q, agent) != "" {
		return false, nil // stays unannounced; told when it wakes or moves
	}
	handle := must(db.Terminal(q, agent))
	if handle == "" || !e.Terminal.Available() || !e.Terminal.Alive(handle) || !e.sessionPresent(agent) || !e.Terminal.TypeableNow(handle) {
		return false, nil
	}
	text := AnswersText(answered)
	e.withTerminal(agent, func() {
		if e.typeGate(q, agent, handle, true, false) != "" {
			return
		}
		if sent, _ = e.send(handle, text); sent {
			e.markTyped(q, agent, nil)
		}
	})
	if sent {
		e.recordAnswers(q, agent, answered)
	}
	return sent, nil
}

// NotifyCancelled types at an agent that a request it was handed has been closed under it, with delivery's gates; (typed, why not).
func (e *Env) NotifyCancelled(q db.Q, row *db.Request, verdict string) (typed bool, why string, err error) {
	defer Catch(&err)
	agent := row.Target
	if e.structuredAgent(agent) {
		state, why := e.sendOnce(q, agent, fmt.Sprintf("cancelled:%d", row.ID), cancelledText(q, row, verdict), structured.Now)
		return state != NotSent, why, nil
	}
	handle := must(db.Terminal(q, agent))
	if held := e.typingHold(q, agent); held != "" {
		return false, held, nil
	}
	if handle == "" || !e.Terminal.Available() || !e.Terminal.Alive(handle) {
		return false, agent + " has no terminal", nil
	}
	if !e.sessionPresent(agent) {
		return false, agent + " has no running session", nil
	}
	if !e.Terminal.TypeableNow(handle) {
		return false, agent + " is mid-work or at a prompt", nil
	}
	locked := e.withTerminal(agent, func() {
		if g := e.typeGate(q, agent, handle, true, false); g != "" {
			why = g
			return
		}
		typed, _ = e.send(handle, cancelledText(q, row, verdict))
		if typed {
			e.markTyped(q, agent, nil)
		} else {
			why = "the send failed"
		}
	})
	if !locked {
		return false, "something else is typing at " + agent, nil
	}
	return typed, why, nil
}

// cancelledText is what an agent is told when a request it was handed is closed under it.
func cancelledText(q db.Q, row *db.Request, verdict string) string {
	held := ""
	if row.Kind.String == "task" {
		if t := taskOf(q, row); t != nil {
			st := must(db.TaskState(q, t))
			held = fmt.Sprintf(", and T%d is %s - `hl task list` shows what you hold", t.ID, st)
		}
	}
	return fmt.Sprintf("#%d from %s was cancelled: %s. Stop work on it; it is closed and needs no resolve%s. `hl read %d` shows it.",
		row.ID, row.Requester, verdict, held, row.ID)
}

func idList(ids []int64) string {
	parts := make([]string, len(ids))
	for i, r := range ids {
		parts[i] = strconv.FormatInt(r, 10)
	}
	return strings.Join(parts, ",")
}
