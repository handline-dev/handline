package supervisor

import (
	"fmt"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/structured"
)

// A message told to an agent's session at once: the socket's `message` verb and TellNow for a request, and the pending row that survives
// a restart (daemon/messaging.py). Nothing here waits for idle: the session queues a line sent mid-turn and reads it once the turn ends.
// Only a closed or missing session holds a message, and a closed session is woken first. What is held stays owed - on its row (a request) or in pending_messages (a bare line) - and the tick tries again. Tasks are not messages: they keep
// DeliverQueued's one-at-a-time rule.

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
	if e.structuredAgent(agent) {
		// Joins the running turn; the ledger holds the operation, so two callers never write the row twice.
		if state, why := e.sendOnce(q, agent, fmt.Sprintf("pending:%d", rowID), text, structured.Now); state == NotSent {
			return false, why
		}
		check(db.DeletePending(q, rowID))
		return true, "sent"
	}
	return false, agent + " has no live session"
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
	if !e.hasSession(agent) {
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
	if e.structuredAgent(agent) {
		if !e.Agents.Live(agent) {
			e.Lifecycle.StartForWork(q, agent) // a structured agent has no terminal to look for: starting it opens its session
		}
	} else {
		state := stateOf(q, agent)
		if !(state != nil && state.Terminal.String != "") {
			e.Lifecycle.StartForWork(q, agent)
			state = stateOf(q, agent)
		}
		if state != nil && state.ClosedAt.Valid && state.ClosedAt.Int64 != 0 {
			e.Lifecycle.WakeAgent(q, agent)
		}
	}
	// A resume's first prompt may already have told it (WaitingPrompt); a fresh start does not.
	row = must(db.GetRequest(q, rid))
	if row.State != "queued" {
		return "answered meanwhile", nil
	}
	if row.DeliveredAt.Valid {
		return "already told", nil
	}
	if e.structuredAgent(agent) {
		return e.tellNowStructured(q, row)
	}
	return "queued - no live session; told when it is up", nil
}

// TellUndelivered tells, each tick, the messages nobody has told the agent yet: one that reached it while it had no session, or whose edge
// was still to be checked. Tasks are not these, unless marked send-now: they keep their own turn.
func (e *Env) TellUndelivered(q db.Q, agent string) (err error) {
	defer Catch(&err)
	state := stateOf(q, agent)
	if !e.openSession(agent, state) || state != nil && state.ClosedAt.Valid && state.ClosedAt.Int64 != 0 {
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

// MigrateOldMessages runs at daemon start: a say the Python daemon told with no ledger of its own is settled, and a request it told but never
// marked typed is typed then. A say the daemon has yet to tell, or whose send the session ledger tracks, is not an old say: it is owed, and
// the tick tells it (TellUndelivered) or the ledger settles it (confirmed, lost or unknown). Settling those at every start lost the
// messages sent minutes before a restart.
func (e *Env) MigrateOldMessages(q db.Q) (err error) {
	defer Catch(&err)
	ids := must(idsOf(q, "SELECT r.id FROM requests r WHERE r.state = 'queued' AND r.kind = 'say' AND r.target != ? AND r.delivered_at IS NOT NULL "+
		"AND NOT EXISTS (SELECT 1 FROM operations o WHERE o.kind = 'send' AND o.agent = r.target AND o.id LIKE 'send:' || r.target || ':request:%' "+
		"AND ',' || substr(o.id, length('send:' || r.target || ':request:') + 1) || ',' LIKE '%,' || r.id || ',%')", db.Human))
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
