package supervisor

import (
	"fmt"
	"strconv"
	"strings"
	"sync"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/structured"
)

// The one send path to a structured session (docs/design/structured-sessions.md, "Delivery"): the same for a person's message, another agent's,
// the daemon's own notices and a task. A message goes out once under an operation id; a retry that finds it already out writes nothing; one
// that was written and not confirmed when its session ended is marked unknown, never written again, and the sender of each request in it is told. A pending message and a batch of answers have no sender to tell: the delivery_unknown event records them.
//
// The ledger is the cursors table, one row per operation, so nothing here needs a schema of its own: `send:<agent>:<op>` holds the state.
// The Agents port's contract: an error from Send means nothing was written. A message written and never echoed stays Written; ending it needs
// a deadline from the session layer (S1).

// The states of a send, as the ledger holds them.
const (
	sendWritten   = 1 // written to the session, not yet echoed
	sendConfirmed = 2 // the session echoed it
	sendUnknown   = 3 // the session ended after the write and before the echo: it may or may not have run
	sendHeld      = 4 // held behind a running turn, not yet written
)

// SendState is where a send is, in words.
type SendState string

const (
	NotSent   SendState = ""       // nothing was written: no live session, or the write failed
	Held      SendState = "queued" // held behind a running turn
	Written   SendState = "sent"   // written, not yet confirmed
	Confirmed SendState = "confirmed"
	Unknown   SendState = "unknown" // written, and the session ended before it confirmed: not written again
)

func ledgerKey(agent, op string) string { return "send:" + agent + ":" + op }

func stateOfLedger(v int64) SendState {
	switch v {
	case sendWritten:
		return Written
	case sendConfirmed:
		return Confirmed
	case sendUnknown:
		return Unknown
	case sendHeld:
		return Held
	}
	return NotSent
}

// structuredAgent says whether the agent is delivered to by the send path.
func (e *Env) structuredAgent(agent string) bool {
	return e.Agents != nil && e.Agents.Structured(agent)
}

// requestOp is the operation id of a message that tells the agent about these requests: the same requests are the same operation.
func requestOp(rids ...int64) string { return "request:" + idList(rids) }

func ridsOfOp(op string) []int64 {
	list, ok := strings.CutPrefix(op, "request:")
	if !ok {
		return nil
	}
	var out []int64
	for _, p := range strings.Split(list, ",") {
		if n, err := strconv.ParseInt(p, 10, 64); err == nil {
			out = append(out, n)
		}
	}
	return out
}

// answersOp is the operation id of a message that tells the agent the answers to these requests of its own.
func answersOp(rids []int64) string { return "answers:" + idList(rids) }

func answerIDs(op string) ([]int64, bool) {
	list, ok := strings.CutPrefix(op, "answers:")
	if !ok {
		return nil, false
	}
	var out []int64
	for _, p := range strings.Split(list, ",") {
		if n, err := strconv.ParseInt(p, 10, 64); err == nil {
			out = append(out, n)
		}
	}
	return out, true
}

// notHeld is the answers not already in a held batch: those are told when their batch goes out, not in a second one.
func (e *Env) notHeld(q db.Q, agent string, answered []*db.Request) []*db.Request {
	prefix := ledgerKey(agent, "answers:")
	rows := must(q.Query("SELECT name FROM cursors WHERE name LIKE ? ESCAPE '\\' AND row_id = ?", likeEscape(prefix)+"%", sendHeld))
	held := map[int64]bool{}
	for rows.Next() {
		var name string
		check(rows.Scan(&name))
		ids, _ := answerIDs(strings.TrimPrefix(name, ledgerKey(agent, "")))
		for _, id := range ids {
			held[id] = true
		}
	}
	check(rows.Err())
	rows.Close()
	var out []*db.Request
	for _, r := range answered {
		if !held[r.ID] {
			out = append(out, r)
		}
	}
	return out
}

// announced records that these answers were told: the requests they settle are not announced again.
func (e *Env) announced(q db.Q, agent string, ids []int64) {
	check(db.MarkAnnounced(q, ids))
	must(db.AddEvent(q, agent, "answered", db.KV{K: "count", V: len(ids)}))
	e.log("  %s: told about %d answer(s)", agent, len(ids))
}

// nextOp is a fresh operation id for a message nothing else names (a notice, a nudge).
func (e *Env) nextOp(q db.Q, prefix string) string {
	n := must(db.Cursor(q, "send_seq")) + 1
	check(db.SetCursor(q, "send_seq", n))
	return fmt.Sprintf("%s:%d", prefix, n)
}

// sendOnce sends text to a structured agent under op. A repeat of an operation answers its state and writes nothing. NotSent says nothing
// was written (no live session, or the session refused the write) and the caller may try again; any other state means the daemon owns the
// message from here and must not send it again.
func (e *Env) sendOnce(q db.Q, agent, op, text string, d structured.Delivery) (SendState, string) {
	lock := e.sendLock(agent)
	lock.Lock()
	defer lock.Unlock()
	key := ledgerKey(agent, op)
	if v := must(db.Cursor(q, key)); v != 0 {
		return stateOfLedger(v), ""
	}
	if !e.Agents.Live(agent) {
		return NotSent, agent + " has no live session"
	}
	check(db.SetCursor(q, key, sendWritten)) // recorded before the write: a crash after it must find it
	rcpt, err := e.Agents.Send(agent, op, text, d)
	if err != nil {
		check(db.ClearCursor(q, key))
		return NotSent, err.Error()
	}
	if rcpt.State == structured.Withdrawn { // its sender took it back: nothing was or will be written
		check(db.ClearCursor(q, key))
		return NotSent, "withdrawn"
	}
	// The echo can come before Send answers: only a row still Written moves on, so a Confirmed one is never taken back to Held.
	switch rcpt.State {
	case structured.Queued:
		check(moveLedger(q, key, sendWritten, sendHeld))
	case structured.Confirmed:
		check(moveLedger(q, key, sendWritten, sendConfirmed))
	}
	return stateOfLedger(must(db.Cursor(q, key))), ""
}

// moveLedger sets a ledger row to to if it is still from, in one statement.
func moveLedger(q db.Q, key string, from, to int64) error {
	_, err := q.Exec("UPDATE cursors SET row_id = ?, updated = ? WHERE name = ? AND row_id = ?", to, db.Now(), key, from)
	return err
}

// sendLock is agent's send lock: a write to one stuck CLI holds up that agent's sends and no one else's.
func (e *Env) sendLock(agent string) *sync.Mutex {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.sendLocks == nil {
		e.sendLocks = map[string]*sync.Mutex{}
	}
	if e.sendLocks[agent] == nil {
		e.sendLocks[agent] = &sync.Mutex{}
	}
	return e.sendLocks[agent]
}

// SessionConfirmed is what the session layer calls when the CLI echoes a message: the operation is delivered, and the requests it told the
// agent about have landed.
func (e *Env) SessionConfirmed(q db.Q, agent, op string) (err error) {
	defer Catch(&err)
	key := ledgerKey(agent, op)
	was := must(db.Cursor(q, key))
	check(moveLedger(q, key, sendWritten, sendConfirmed))
	check(moveLedger(q, key, sendHeld, sendConfirmed))
	if (was != sendWritten && was != sendHeld) || must(db.Cursor(q, key)) != sendConfirmed {
		return nil
	}
	for _, rid := range ridsOfOp(op) {
		row := must(db.GetRequest(q, rid))
		if row == nil || row.LandedAt.Valid {
			continue
		}
		check(db.MarkLanded(q, rid))
		must(db.AddEvent(q, agent, "landed", db.KV{K: "request_id", V: rid}))
	}
	if ids, ok := answerIDs(op); ok && was == sendHeld { // a written batch was announced when it went out
		e.announced(q, agent, ids)
	}
	return nil
}

// SessionLost is what the session layer calls when an agent's session ended. A message written and not confirmed is unknown: the sender is
// told and it is not written again. A message that was only held was never written and is owed again.
func (e *Env) SessionLost(q db.Q, agent string) (err error) {
	defer Catch(&err)
	prefix := ledgerKey(agent, "")
	rows := must(q.Query("SELECT name, row_id FROM cursors WHERE name LIKE ? ESCAPE '\\' ORDER BY name", likeEscape(prefix)+"%"))
	type lost struct {
		op string
		v  int64
	}
	var ops []lost
	for rows.Next() {
		var name string
		var v int64
		check(rows.Scan(&name, &v))
		if v == sendWritten || v == sendHeld {
			ops = append(ops, lost{strings.TrimPrefix(name, prefix), v})
		}
	}
	check(rows.Err())
	rows.Close()
	for _, l := range ops {
		key := ledgerKey(agent, l.op)
		if l.v == sendHeld {
			check(db.ClearCursor(q, key))
			for _, rid := range ridsOfOp(l.op) {
				check(db.ClearDelivered(q, rid))
			}
			continue
		}
		check(db.SetCursor(q, key, sendUnknown))
		e.deliveryUnknown(q, agent, l.op)
	}
	return nil
}

func likeEscape(s string) string {
	return strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`).Replace(s)
}

// deliveryUnknown records that a write may or may not have run, and tells the sender of each request in it (ops with no request name no one).
func (e *Env) deliveryUnknown(q db.Q, agent, op string) {
	must(db.AddEvent(q, agent, "delivery_unknown", db.KV{K: "op", V: op}))
	told := map[string]bool{}
	for _, rid := range ridsOfOp(op) {
		row := must(db.GetRequest(q, rid))
		if row == nil || told[row.Requester] || row.Requester == db.NoticeRequester {
			continue
		}
		told[row.Requester] = true
		text := fmt.Sprintf("#%d to %s was written to its session, and the session ended before it confirmed it: it may or may not have been read. "+
			"It is not sent again; ask %s whether it saw it, or send it again.", rid, agent, agent)
		if row.Requester == db.Human {
			must(db.AddRequest(q, db.NewRequest{Requester: db.NoticeRequester, Target: db.Human, Body: text, Response: "fire_and_forget", Kind: "say"}))
			continue
		}
		e.typeSay(q, row.Requester, text, "delivery unknown")
	}
}

// hasSession says whether the agent has a running session: a structured agent's is open, a terminal agent's is in its terminal.
func (e *Env) hasSession(agent string) bool {
	if e.structuredAgent(agent) {
		return e.Agents.Live(agent)
	}
	return e.sessionPresent(agent)
}

// tellNowStructured tells a structured agent about request row at once, joining the running turn.
func (e *Env) tellNowStructured(q db.Q, row *db.Request) (what string, err error) {
	what, _, err = e.tellStructured(q, row, structured.Now)
	return what, err
}

// tellStructured tells a structured agent about request row. The row is marked delivered before the write (it closes withdraw and replace's
// race) and owed again only when nothing was written; a message that was written is never written again. A Queue message held behind a
// running turn is not delivered: the row is left as it was, and SessionWritten marks it when the session writes it.
func (e *Env) tellStructured(q db.Q, row *db.Request, d structured.Delivery) (what string, state SendState, err error) {
	defer Catch(&err)
	agent, rid := row.Target, row.ID
	if !e.Agents.Live(agent) {
		return "queued - no live session; told when it is up", NotSent, nil
	}
	if !must(db.MarkDelivered(q, rid)) {
		return "withdrawn first", NotSent, nil
	}
	folded := foldedNotices(q, agent)
	state, why := e.sendOnce(q, agent, requestOp(rid), tellText(q, row, 1, nil, false, folded), d)
	switch state {
	case NotSent:
		check(db.ClearDelivered(q, rid))
		return "queued - " + why, state, nil
	case Held:
		check(db.ClearDelivered(q, rid))
		return "held behind the running turn", state, nil
	}
	session := e.liveSession(q, agent)
	check(db.MarkNoticesShown(q, requestIDs(folded), session))
	check(db.MarkToldAhead(q, agent, rid, requestIDs(must(db.QueuedFor(q, agent))))) // becoming the head is not news
	check(db.MarkTyped(q, rid, session))
	e.NoteTyped(rid)
	must(db.AddEvent(q, agent, "notified", db.KV{K: "request_id", V: rid}, db.KV{K: "more_waiting", V: 0}))
	if state == Confirmed {
		check(db.MarkLanded(q, rid))
		e.FinishSay(q, row) // a say that was only written can still be taken back, with its confirm
	}
	e.log("  %s: told about #%d at once", agent, rid)
	return "sent", state, nil
}
