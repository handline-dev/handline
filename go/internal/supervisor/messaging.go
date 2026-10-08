package supervisor

import (
	"fmt"

	"github.com/handline/handline/go/internal/db"
)

// A message typed into an agent's terminal at once: the socket's `message` verb and TellNow for a request, and the pending row that survives
// a restart (daemon/messaging.py). Nothing here waits for idle: Claude Code queues a line typed mid-turn and reads it once the turn ends.
// Only a screen blocker, the wall, or a closed session holds a message, and a closed session is woken first. Never Esc. What is held stays
// owed - on its row (a request) or in pending_messages (a bare line) - and the tick tries again. Tasks are not messages: they keep
// DeliverQueued's one-at-a-time rule.

// CanTypeNow is whether nothing is on screen a typed line would answer instead, and not at the wall: the two things immediate typing waits
// for. Not TypeableNow, which also requires idle.
func (e *Env) canTypeNow(q db.Q, agent, handle string) (bool, string) {
	if !e.Terminal.Available() {
		return false, e.Terminal.Missing()
	}
	if reason := e.typingHold(q, agent); reason != "" {
		return false, reason
	}
	if b := e.Terminal.ScreenBlocker(handle); b != nil {
		return false, b.Reason()
	}
	return true, ""
}

// FinishSay: a say is finished once it is typed. Nothing is owed on it, so it is settled quietly with no verdict (a verdict is drawn as the
// target's reply, and nobody replied). Anything else typed for a request stays open until it is answered.
func (e *Env) FinishSay(q db.Q, row *db.Request) {
	if row.Kind.String == "say" {
		if _, _, err := e.Resolve(q, ResolveIn{RID: row.ID}); err != nil {
			e.log("  #%d: settling the say failed: %v", row.ID, err)
		}
	}
}

// deliverRow tries to type text (pending row rowID) at the agent right now; true only once it is sent, with the row deleted in the same lock
// hold that sent it, so a message typed here and a concurrent DeliverPending never type the row twice.
func (e *Env) deliverRow(q db.Q, agent string, rowID int64, text string) (bool, string) {
	handle := must(db.Terminal(q, agent))
	if handle == "" {
		return false, agent + " has no terminal"
	}
	if !e.Terminal.Available() || !e.Terminal.Alive(handle) {
		return false, fmt.Sprintf("%s's terminal is gone", agent)
	}
	if !e.sessionPresent(agent) {
		return false, agent + " has no running session"
	}
	if ok, why := e.canTypeNow(q, agent, handle); !ok {
		return false, why
	}
	ok, why := false, ""
	locked := e.withTerminal(agent, func() {
		// A concurrent caller for the same row already sent and deleted it while this one waited for the lock.
		if must(db.GetPending(q, rowID)) == nil {
			ok, why = true, "already typed"
			return
		}
		// Re-read once the lock is actually held: a reading taken before asking for it can already be stale.
		if can, w := e.canTypeNow(q, agent, handle); !can {
			why = w
			return
		}
		sent, err := e.send(handle, text)
		if !sent {
			if err == "" {
				err = "the send failed"
			}
			why = err
			return
		}
		e.markTyped(q, agent, nil)
		check(db.DeletePending(q, rowID))
		ok, why = true, "typed"
	})
	if !locked {
		return false, agent + ": something else is using its terminal"
	}
	return ok, why
}

// SendMessage is the socket's `message` verb. The pending row is written first (it is the one thing a reboot must not lose), then delivery is
// tried at once. ok is true once the daemon owns getting text to the agent: typed already, or left in pending_messages for DeliverPending or a
// wake started here to pick up.
func (e *Env) SendMessage(q db.Q, agent, text string) (ok bool, msg string, err error) {
	defer Catch(&err)
	rowID := must(db.AddPending(q, agent, text, e.now()))
	sent, why := e.deliverRow(q, agent, rowID, text)
	if sent {
		return true, agent + ": typed", nil
	}
	if !e.sessionPresent(agent) {
		woke, wakeWhy := e.Lifecycle.WakeAgent(q, agent)
		if woke {
			if sent, why = e.deliverRow(q, agent, rowID, text); sent {
				return true, agent + ": woken and typed", nil
			}
			return true, fmt.Sprintf("%s: queued - woken, will type once free (%s)", agent, why), nil
		}
		return true, fmt.Sprintf("%s: queued - not woken (%s)", agent, wakeWhy), nil
	}
	return true, fmt.Sprintf("%s: queued - %s", agent, why), nil
}

// TellNow tells an agent about request rid at once, the way SendMessage types a line: the notice goes out even mid-turn, and only a screen
// blocker, the wall or a closed session holds it. What is held stays on its row, undelivered, and the tick tries again (TellUndelivered); the
// row is marked delivered only once it is typed, so it can be withdrawn until then and counts as owed until then. Tasks do not come here,
// except one marked send-now. Returns what happened.
func (e *Env) TellNow(q db.Q, rid int64) (what string, err error) {
	defer Catch(&err)
	row := must(db.GetRequest(q, rid))
	if row == nil || row.State != "queued" {
		return "no longer queued", nil
	}
	agent := row.Target
	if agent == db.Human || !e.registered(agent) {
		return "not an agent", nil
	}
	if ok, err := e.EnforceEdges(q, agent); err != nil {
		return "", err
	} else if !ok {
		return "held: team.yaml is not trusted", nil
	}
	row = must(db.GetRequest(q, rid))
	if row.State != "queued" {
		return "refused at delivery", nil
	}
	state := stateOf(q, agent)
	if !(state != nil && state.Terminal.String != "") {
		e.Lifecycle.StartForWork(q, agent)
		state = stateOf(q, agent)
	}
	if state != nil && state.ClosedAt.Valid && state.ClosedAt.Int64 != 0 {
		e.Lifecycle.WakeAgent(q, agent)
	}
	// A resume's first prompt may already have told it (WaitingPrompt); a fresh start does not.
	row = must(db.GetRequest(q, rid))
	if row.State != "queued" {
		return "answered meanwhile", nil
	}
	if row.DeliveredAt.Valid {
		return "already told", nil
	}
	handle := must(db.Terminal(q, agent))
	if handle == "" || !e.sessionPresent(agent) {
		return "queued - no running session; told when it is up", nil
	}
	if !e.Terminal.Available() || !e.Terminal.Alive(handle) {
		return fmt.Sprintf("queued - %s's terminal is gone", agent), nil
	}
	if ok, why := e.canTypeNow(q, agent, handle); !ok {
		return "queued - " + why, nil
	}
	result := ""
	var told *db.Request
	locked := e.withTerminal(agent, func() {
		row = must(db.GetRequest(q, rid))
		if row.State != "queued" || row.DeliveredAt.Valid {
			result = "already told"
			return
		}
		if ok, why := e.canTypeNow(q, agent, handle); !ok {
			result = "queued - " + why
			return
		}
		if !must(db.MarkDelivered(q, rid)) {
			result = "withdrawn first"
			return
		}
		if e.landedAlready(q, agent, rid) { // the transcript first
			check(db.MarkTyped(q, rid, e.liveSession(q, agent)))
			check(db.MarkLanded(q, rid))
			result = "already in its transcript"
			return
		}
		folded := foldedNotices(q, agent)
		sent, err := e.send(handle, tellText(q, row, 1, nil, false, folded))
		if !sent {
			check(db.ClearDelivered(q, rid))
			if err == "" {
				err = "the send failed"
			}
			result = "queued - " + err
			return
		}
		e.markTyped(q, agent, nil)
		check(db.MarkNoticesShown(q, requestIDs(folded), e.liveSession(q, agent)))
		check(db.MarkToldAhead(q, agent, rid, requestIDs(must(db.QueuedFor(q, agent))))) // becoming the head is not news
		check(db.MarkTyped(q, rid, e.liveSession(q, agent)))
		e.NoteTyped(rid)
		must(db.AddEvent(q, agent, "notified", db.KV{K: "request_id", V: rid}, db.KV{K: "more_waiting", V: 0}))
		told = row
	})
	if !locked {
		return fmt.Sprintf("queued - %s: something else is using its terminal", agent), nil
	}
	if told == nil {
		return result, nil
	}
	e.log("  %s: told about #%d at once", agent, rid)
	e.FinishSay(q, told)
	return "typed", nil
}

// TellUndelivered tells, each tick, the messages nobody has told the agent yet: one that reached it while it had no session, or whose edge
// was still to be checked. Tasks are not these, unless marked send-now: they keep their own turn.
func (e *Env) TellUndelivered(q db.Q, agent string) (err error) {
	defer Catch(&err)
	state := stateOf(q, agent)
	if !(state != nil && state.Terminal.String != "") || state.ClosedAt.Valid && state.ClosedAt.Int64 != 0 {
		return nil // starting or waking it is the supervisor's own step, with its own gates
	}
	for _, row := range must(db.UndeliveredFor(q, agent)) {
		if row.Kind.String != "task" || row.SendNow.Valid {
			if _, err := e.TellNow(q, row.ID); err != nil {
				return err
			}
		}
	}
	return nil
}

// DeliverPending is one tick's worth of what SendMessage could not type at once: the oldest pending row, if the agent's terminal will take it
// now. One per tick, so a session that just cleared a dialog hears the oldest thing waiting first, not everything piled up as one block.
func (e *Env) DeliverPending(q db.Q, agent string) (sent bool, err error) {
	defer Catch(&err)
	rows := must(db.ListPending(q, agent))
	if len(rows) == 0 {
		return false, nil
	}
	row := rows[0]
	ok, _ := e.deliverRow(q, agent, row.ID, row.Text)
	if ok {
		e.log("  %s: typed a pending message (#%d)", agent, row.ID)
	}
	return ok, nil
}

// MigrateOldMessages runs once at daemon start: an old say was told, or never will be, and is settled. An old ask needs nothing. A request the
// daemon before landing was tracked told has delivered_at and never a typed_at, so the landing sweep never saw it: told then is typed then.
func (e *Env) MigrateOldMessages(q db.Q) (err error) {
	defer Catch(&err)
	ids := must(idsOf(q, "SELECT id FROM requests WHERE state = 'queued' AND kind = 'say' AND target != ?", db.Human))
	for _, id := range ids {
		if _, _, err := e.Resolve(q, ResolveIn{RID: id}); err != nil {
			return err
		}
		e.log("  #%d: an old say, settled", id)
	}
	_, err = q.Exec("UPDATE requests SET typed_at = delivered_at WHERE state = 'queued' AND target != ? AND delivered_at IS NOT NULL AND typed_at IS NULL AND landed_at IS NULL", db.Human)
	return err
}

// ReloadPending runs once at daemon start: try every pending row at once, for whatever agent is already running and free to hear it.
func (e *Env) ReloadPending(q db.Q) (err error) {
	defer Catch(&err)
	seen := map[string]bool{}
	var agents []string
	for _, r := range must(db.ListPending(q, "")) {
		if !seen[r.Agent] {
			seen[r.Agent] = true
			agents = append(agents, r.Agent)
		}
	}
	sortStrings(agents)
	for _, a := range agents {
		if _, err := e.DeliverPending(q, a); err != nil {
			return err
		}
	}
	return nil
}
