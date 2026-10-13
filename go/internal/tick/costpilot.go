package tick

import (
	"database/sql"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
)

// The cost pilot: two ways to spend fewer cache tokens on an idle agent, each off until an agent's own agent.yaml turns it on, so every
// other agent is the control.
//
// Renewal (cache_renewal: true). An open idle session whose cache lives an hour is asked for its handoff note a minute or two before the
// cache lapses. That turn reads the context at the cached price and writes a response, which restarts the hour. At most MaxRenewals per
// idle stretch, and never over RenewCtxMax tokens of context: past that a lapse is cheaper to take than a read, and the fold is the answer.
// It replaces the single ask at 90% for this agent. A stretch ends at a message someone typed; the ask's own turn does not end it.
//
// Fold (idle_fold: true). An open idle session that holds no task, has nothing queued, and has a context of idle_fold_tokens (150k) or
// more is closed fresh after idle_fold_after_s (10 minutes) of silence: its next wake is a new conversation that starts from the handoff
// record, with the note the agent wrote while its cache was warm. The model fold is not run here: the daemon tick must not wait on it.
//
// Every renewal and fold is an event carrying the figures it was decided on. Decisions are levels read from the record, never edges: a
// renewal count is read back from the events.
const (
	RenewalKey = "cache_renewal"
	FoldKey    = states.FoldKind
	// FreshKey (fresh_after_task: true) starts a new session after each finished task: the context handoff, once the agent holds no
	// other task. Off until an agent's own agent.yaml turns it on; the auditors, whose every review is a fresh look, are who it is for.
	FreshKey = "fresh_after_task"
	// FreshKind is the kind a handoff for it carries: not a wall and not a full context, so it parks for no time and wakes at once.
	FreshKind = "task_done"
	// RenewLeadS: a renewal is asked in the last stretch of the cache's life, wide enough that one 30-second tick cannot step over it.
	RenewLeadS   = 150
	MaxRenewals  = 2
	RenewCtxMax  = 150_000
	FoldCtxMin   = 150_000
	FoldIdleS    = 600
	longTTLS     = 3600 // cachettl.LONG_TTL_S
	renewalsRows = 20
)

// CacheClock is cachettl.read: when the prompt cache lapses. TTL is nil when unknown; Left is nil when the clock cannot say.
type CacheClock struct {
	TTL  *int64
	Left func(now int64) *float64
}

// CostPorts are the foreign calls the cost pilot makes.
type CostPorts interface {
	// ReadCache is cachettl.read of the status line's transcript; nil when unreadable.
	ReadCache(transcriptPath string) *CacheClock
}

// CostPilot reads an agent's own switches; there is no global one, and absent means off.
type CostPilot struct{ Ports CostPorts }

func (c *CostPilot) own(t *Tick, agent, key string) any { return t.Config.AgentConfig(agent)[key] }

func (c *CostPilot) flag(t *Tick, agent, key string) bool {
	b, ok := c.own(t, agent, key).(bool)
	return ok && b
}

// RenewalOn is the agent's switch.
func (c *CostPilot) RenewalOn(t *Tick, agent string) bool { return c.flag(t, agent, RenewalKey) }

func (c *CostPilot) ownInt(t *Tick, agent, key string, def int64) int64 {
	switch v := c.own(t, agent, key).(type) {
	case int:
		if v > 0 {
			return int64(v)
		}
	case int64:
		if v > 0 {
			return v
		}
	}
	return def
}

// RenewalsInStretch is how many renewals this idle stretch has had. rows are the agent's UserPromptSubmit, SessionStart and cache_renewal
// events' kinds, newest first. The stretch reaches back to the last message someone typed or the last session start; a prompt straight
// after a renewal is that renewal's own turn and does not end it.
func RenewalsInStretch(kinds []string) int {
	n := 0
loop:
	for i, kind := range kinds {
		switch kind {
		case "cache_renewal":
			n++
		case "SessionStart":
			break loop
		case "UserPromptSubmit":
			older := ""
			if i+1 < len(kinds) {
				older = kinds[i+1]
			}
			if older != "cache_renewal" {
				break loop
			}
		}
	}
	return n
}

func (c *CostPilot) renewals(t *Tick, agent string) int {
	rows, err := t.Q.Query("SELECT kind FROM events WHERE agent = ? AND kind IN ('UserPromptSubmit', 'SessionStart', 'cache_renewal') ORDER BY id DESC LIMIT ?", agent, renewalsRows)
	check(err)
	defer rows.Close()
	var kinds []string
	for rows.Next() {
		var k string
		check(rows.Scan(&k))
		kinds = append(kinds, k)
	}
	check(rows.Err())
	return RenewalsInStretch(kinds)
}

// openIdle is an open session that can take a turn now and runs nothing behind its prompt.
func (c *CostPilot) openIdle(t *Tick, agent string, row *db.AgentState) bool {
	return row != nil && !closedAt(row) && row.State == states.Idle && t.Usage.AtLimit(t.Q, agent) == nil &&
		!t.Delivery.HasBackgroundWork(t.Q, agent)
}

// RenewalDue is whether this session is due a renewal now: the switch is on, it is open and idle, its cache is an hour's and in its last
// stretch, its context is known and not over RenewCtxMax, no ask of its is still waiting to be taken, and the stretch has had fewer than
// MaxRenewals.
func (c *CostPilot) RenewalDue(t *Tick, agent string, row *db.AgentState, now int64, st Status) bool {
	if !c.RenewalOn(t, agent) || !t.Handoff.NoteEnabled(agent) || !c.openIdle(t, agent, row) {
		return false
	}
	if st.TrustedCtxUsed == nil || *st.TrustedCtxUsed > RenewCtxMax {
		return false // unreadable is not small: fail closed
	}
	clock := c.Ports.ReadCache(st.TranscriptPath)
	if clock == nil || clock.TTL == nil || *clock.TTL < longTTLS {
		return false
	}
	left := clock.Left(now)
	if left == nil || !(float64(t.Handoff.AskMarginS()) <= *left && *left < RenewLeadS) {
		return false
	}
	if turns, ok := t.Handoff.NoteAsked(t.Q, agent); ok && turns == 0 {
		return false // asked, and the turn has not begun
	}
	return c.renewals(t, agent) < MaxRenewals
}

// Renew asks for the note if a renewal is due, and records it. True when the ask was sent.
func (c *CostPilot) Renew(t *Tick, agent string, row *db.AgentState, now int64, st Status) bool {
	if !c.RenewalDue(t, agent, row, now, st) {
		return false
	}
	count := c.renewals(t, agent) + 1
	if !t.Handoff.NoteAsk(t.Q, agent, "cache") {
		return false
	}
	var left any
	if clock := c.Ports.ReadCache(st.TranscriptPath); clock != nil {
		if l := clock.Left(now); l != nil {
			left = int64(*l)
		}
	}
	var ctx any
	if st.TrustedCtxUsed != nil {
		ctx = *st.TrustedCtxUsed
	}
	check(eventErr(db.AddEvent(t.Q, agent, RenewalKey, db.KV{K: "n", V: count}, db.KV{K: "of", V: MaxRenewals}, db.KV{K: "ctx", V: ctx}, db.KV{K: "cache_left_s", V: left})))
	t.logf("  %s: cache renewal %d of %d", agent, count, MaxRenewals)
	return true
}

func eventErr(_ int64, err error) error { return err }

// FoldDue is whether this session is due a fold now: the switch is on, it is open and idle past its fold wait, it holds no task and has
// nothing queued or waiting to be told, its context is known and at the fold size, there is a session to take the record from, and no
// close of it failed lately.
func (c *CostPilot) FoldDue(t *Tick, agent string, row *db.AgentState, now int64, st Status) bool {
	if !c.flag(t, agent, FoldKey) || !c.openIdle(t, agent, row) || st.SessionID == "" {
		return false
	}
	if st.TrustedCtxUsed == nil || int64(*st.TrustedCtxUsed) < c.ownInt(t, agent, "idle_fold_tokens", FoldCtxMin) {
		return false
	}
	if _, has := must2(db.CurrentTask(t.Q, agent)); has || len(must(db.QueuedFor(t.Q, agent))) > 0 {
		return false // not at a task boundary
	}
	if t.Lifecycle.WaitingFor(t.Q, agent) {
		return false
	}
	wait := c.ownInt(t, agent, "idle_fold_after_s", FoldIdleS)
	last, have := t.Delivery.IdleSince(t.Q, agent)
	// The note's own turn restarts the idle clock; it is the answer this fold was held for.
	if !t.Handoff.NoteAnsweredTurn(t.Q, agent) && (!have || now-last < wait) {
		return false
	}
	failed, ok := must2(db.LastEventTS(t.Q, agent, "close_failed"))
	return !ok || now-failed >= wait
}

// Fold closes the session fresh and records it. The caller has held the close for the note and checked FoldDue.
func (c *CostPilot) Fold(t *Tick, agent string, now int64, st Status) (bool, string) {
	_, hadNote := t.Handoff.NoteText(t.Q, agent)
	since, have := t.Delivery.IdleSince(t.Q, agent)
	if !have {
		since = now
	}
	idle := now - since
	if idle < 0 {
		idle = 0
	}
	ok, why := t.Lifecycle.CloseFresh(t.Q, agent, st)
	if ok {
		var ctx any
		ctxText := "None"
		if st.TrustedCtxUsed != nil {
			ctx = *st.TrustedCtxUsed
			ctxText = commas(*st.TrustedCtxUsed)
		}
		check(eventErr(db.AddEvent(t.Q, agent, FoldKey, db.KV{K: "ctx", V: ctx}, db.KV{K: "idle_s", V: idle}, db.KV{K: "note", V: hadNote})))
		noNote := ""
		if !hadNote {
			noNote = " (no note)"
		}
		t.logf("  %s: folded - idle %dm at %s tokens, closed fresh%s", agent, idle/60, ctxText, noNote)
	}
	return ok, why
}

// FreshDue is whether this agent is owed a fresh session: its switch is on, its session is open, it finished a task (done, or handed on
// to another owner) since its last session began or was handed off, and it holds no other open task. Mid-turn is the handoff's own concern: it defers to the turn's end
// and asks for the note first, as it does for a full context. A level read from the record, never an edge: it is due on every tick until a
// handoff or a new session is recorded after the task was done.
func (c *CostPilot) FreshDue(t *Tick, agent string, row *db.AgentState) bool {
	if !c.flag(t, agent, FreshKey) || closedAt(row) {
		return false
	}
	var doneAt sql.NullInt64 // the later of its last done task and the last task it handed on
	check(t.Q.QueryRow("SELECT MAX(at) FROM (SELECT MAX(done_at) AS at FROM tasks WHERE owner = ? AND done_evidence IS NOT NULL "+
		"UNION ALL SELECT MAX(ts) FROM events WHERE kind = 'task_handoff' AND json_extract(payload, '$.from') = ?)", agent, agent).Scan(&doneAt))
	if !doneAt.Valid {
		return false
	}
	var open int
	check(t.Q.QueryRow("SELECT COUNT(*) FROM tasks WHERE owner = ? AND "+db.OpenSQL, agent).Scan(&open))
	if open > 0 {
		return false
	}
	var began sql.NullInt64 // the later of the last handoff and the last session start
	check(t.Q.QueryRow("SELECT MAX(at) FROM (SELECT MAX(updated) AS at FROM handoff WHERE agent = ? "+
		"UNION ALL SELECT MAX(ts) FROM events WHERE agent = ? AND kind = 'SessionStart')", agent, agent).Scan(&began))
	return !began.Valid || doneAt.Int64 > began.Int64
}
