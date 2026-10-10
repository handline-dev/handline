package lifecycle

import (
	"fmt"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/term"
	"github.com/handline/handline/go/internal/timefmt"
)

// A failed statement inside the manager unwinds to the exported method that was called, which returns it as the error.
type failure struct{ err error }

func check(err error) {
	if err != nil {
		panic(failure{fmt.Errorf("database: %w", err)})
	}
}

func must[T any](v T, err error) T {
	check(err)
	return v
}

// ConfigError is an agent's own unreadable configuration, which the callers stall by name rather than treat as the daemon's fault.
type ConfigError struct{ Err error }

func (c *ConfigError) Error() string { return c.Err.Error() }
func (c *ConfigError) Unwrap() error { return c.Err }

func catch(err *error) {
	switch r := recover().(type) {
	case nil:
	case failure:
		*err = r.err
	case *ConfigError:
		*err = r
	default:
		panic(r)
	}
}

// Words other code matches on.
const (
	// TypingElsewhere is what a refusal says when the terminal lock is held by whoever is typing.
	TypingElsewhere = "someone else is typing at this agent"
	RestartOwedKind = "restart_owed"
	MoveOwedKind    = "move_owed"
	Unconfirmed     = "the process table could not be confirmed just now"
	PaneMark        = "its terminal was closed with it"
	NothingWaiting  = "nothing is waiting for it; it is woken when something is sent to it"
)

// CloseOpts are the ways a close can differ: a restart keeps the pane and may force a working agent; a fresh start discards the
// conversation on purpose; a restart asked for by name may close a terminal whose screen cannot be read.
type CloseOpts struct {
	KeepPane, Force, Discard, PaneMayClose bool
}

func (m *Manager) cacheExpiredAt(agent, session string, now int64) bool {
	if session == "" || m.Settings.IgnoreCacheExpiry(agent) {
		return false
	}
	if at, ttl, ttlKnown, ok := m.Handoff.CacheClock(m.Q, agent, session); ok && ttlKnown {
		return now-at >= ttl-m.Handoff.CacheMargin(ttl)
	}
	last, ok := must2(db.LastActivity(m.Q, agent))
	return !ok || now-last >= states.ResumeWarmS
}

func must2[A, B any](a A, b B, err error) (A, B) {
	check(err)
	return a, b
}

// CacheExpired: should this closed agent start a fresh conversation rather than resume? Only when its prompt cache can no longer serve
// the resume: the cache's real life less the margin a resume needs to reach its first request. Where the transcript gives no clock,
// ResumeWarmS since it last did anything, or no clock at all, where a wrong "warm" costs the whole transcript and a wrong "cold" costs
// a handoff. Unless the agent's own ignore_cache_expiry says its resume may reuse the cache regardless.
func (m *Manager) CacheExpired(agent, session string, now int64) (yes bool, err error) {
	defer catch(&err)
	return m.cacheExpiredAt(agent, session, now), nil
}

// ShouldClose: is this agent holding memory for nothing? Closing is about the machine, not the account, so the question is whether
// anyone is using this session and whether we can bring it back. It is not when the agent is working or being handed off, a human has
// to look at it, someone is waiting on it (unless it is PARKED, or the cache is already gone), we cannot name the session we would
// resume, a close of it failed within the last idle window, or its own idle window is Never. The session id is the asymmetric trade:
// memory is recoverable the moment anything wants it, and a working context is not.
func (m *Manager) ShouldClose(agent, state string, row *db.AgentState, now int64, sessionID string) (yes bool, err error) {
	defer catch(&err)
	if state == states.Running || state == states.Handoff || state == states.Stalled {
		return false, nil
	}
	if row == nil || (row.ClosedAt.Valid && row.ClosedAt.Int64 != 0) || !m.hasSession(agent, row) {
		return false, nil
	}
	if m.structured(agent) && m.Run.Busy(agent) {
		return false, nil // the CLI is mid-turn whatever the label says
	}
	// deliver_queued runs before this in the same tick, so a PARKED agent that reaches here was not delivered to; its queue is no
	// reason to hold the session open.
	if must(m.Delivery.BgHolds(m.Q, agent, now)) {
		return false, nil // work is still running behind the prompt
	}
	if sessionID == "" {
		return false, nil // nothing to resume; losing the context costs more
	}
	if queued := must(db.QueuedFor(m.Q, agent)); len(queued) > 0 && state != states.Parked && !m.cacheExpiredAt(agent, sessionID, now) {
		return false, nil // work is waiting and the cache is warm; closing would only delay it
	}
	timeout := m.Settings.CloseTimeoutS(agent)
	if timeout == Never {
		return false, nil
	}
	if failed, ok := must2(db.LastEventTS(m.Q, agent, "close_failed")); ok && now-failed < states.IdleCloseS {
		return false, nil // /exit did not end it; asking every tick types it every tick
	}
	last, ok := must2(m.Delivery.IdleSince(m.Q, agent))
	if !ok {
		return false, nil // nothing has ever been seen from it
	}
	// An agent told to finish its background work and shown nothing since is closed now, whatever its window: the time it was given
	// is its grace. So is one that has answered the ask for its handoff note: that turn restarted its idle clock.
	if now-last >= timeout || m.Handoff.AnsweredTurn(m.Q, agent) {
		return true, nil
	}
	return must(m.Delivery.BgOwesNothing(m.Q, agent)) && must(m.Delivery.BgMayStop(m.Q, agent, now)), nil
}

// TriggersApply: would the daemon act on this agent's triggers at all? A closed agent has no session to hand off, and an agent with no
// terminal has nowhere to run. A parked, throttled or degraded row is already past its wall: the tick waits on its wake_at.
func TriggersApply(row *db.AgentState) bool {
	return row != nil && row.Terminal.String != "" && !(row.ClosedAt.Valid && row.ClosedAt.Int64 != 0) &&
		row.State != states.Parked && row.State != states.Throttled && row.State != states.Degraded
}

// TriggersApplyTo is TriggersApply for an agent that may be structured: its session is a live process, not a terminal.
func (m *Manager) TriggersApplyTo(agent string, row *db.AgentState) bool {
	if !m.structured(agent) {
		return TriggersApply(row)
	}
	return row != nil && m.Run.Alive(agent) && !isClosed(row) && row.State != states.Parked && row.State != states.Throttled && row.State != states.Degraded
}

// WakeCause is what the tick wakes a closed agent for.
type WakeCause struct{ Kind, What string }

// ClosedWakeCause is the one list the tick wakes by and the screens read "the next tick wakes it" from. An undelivered request whose
// task is blocked on record is no cause: the wake is refused for it every tick. A request told before the close is none either.
func (m *Manager) ClosedWakeCause(agent string, row *db.AgentState) (c *WakeCause, err error) {
	defer catch(&err)
	if row == nil || !(row.ClosedAt.Valid && row.ClosedAt.Int64 != 0) {
		return nil, nil
	}
	if owed := m.restartOwed(agent, row); owed != nil {
		return &WakeCause{"restart", "a restart that has not started it again"}, nil
	}
	waiting := 0
	for _, r := range must(db.UndeliveredFor(m.Q, agent)) {
		if !must(m.Delivery.HeadTaskBlocked(m.Q, agent, r)) {
			waiting++
		}
	}
	if waiting > 0 {
		return &WakeCause{"work", fmt.Sprintf("%d waiting request(s)", waiting)}, nil
	}
	if n := len(must(db.UnannouncedAnswers(m.Q, agent))); n > 0 {
		return &WakeCause{"answer", fmt.Sprintf("%d answer(s) it asked for", n)}, nil
	}
	if n := len(must(db.ListPending(m.Q, agent))); n > 0 {
		return &WakeCause{"message", fmt.Sprintf("%d pending message(s)", n)}, nil
	}
	return nil, nil
}

// ResumableWithoutTerminal: a closed agent whose pane closed with its session and whose worktree is still there is woken (resuming),
// not started fresh.
func (m *Manager) ResumableWithoutTerminal(row *db.AgentState) bool {
	return row != nil && row.ClosedAt.Valid && row.ClosedAt.Int64 != 0 && (row.Terminal.String == "" || m.structured(row.Agent)) && m.Spawn.WorktreePath(row.Worktree.String) != ""
}

// StartOrWakeNeeded is ("start" | "wake", what) when the daemon owes this agent a session; kind "" otherwise. The one decision the tick
// and the daemon's start both act on: an agent with work waiting and no terminal is started, a closed one with a cause is woken.
func (m *Manager) StartOrWakeNeeded(agent string, row *db.AgentState) (kind, what string, err error) {
	defer catch(&err)
	if row != nil && (row.State == states.Stalled || !states.Evaluated[row.State]) {
		return "", "", nil
	}
	if must(db.InFlightFor(m.Q, agent)) != nil {
		return "", "", nil
	}
	if !m.hasSession(agent, row) && !m.ResumableWithoutTerminal(row) {
		if len(must(db.QueuedFor(m.Q, agent))) > 0 || len(must(db.UnannouncedAnswers(m.Q, agent))) > 0 || len(must(db.ListPending(m.Q, agent))) > 0 {
			return "start", "work waiting", nil
		}
		return "", "", nil
	}
	cause, err := m.ClosedWakeCause(agent, row)
	if err != nil || cause == nil {
		return "", "", err
	}
	return "wake", cause.What, nil
}

// WouldWake: is anything actually trying to wake this closed agent? A deadline already due while parked, throttled or degraded, or a
// cause the main pass wakes a closed row for. A screen gates on this first, so "waiting" only shows next to a wait that is happening.
func (m *Manager) WouldWake(row *db.AgentState) (yes bool, err error) {
	if row == nil || !(row.ClosedAt.Valid && row.ClosedAt.Int64 != 0) {
		return false, nil
	}
	due := row.WakeAt.Valid && row.WakeAt.Int64 != 0 && row.WakeAt.Int64 <= m.now() &&
		(row.State == states.Parked || row.State == states.Throttled || row.State == states.Degraded)
	if due {
		return true, nil
	}
	c, err := m.ClosedWakeCause(row.Agent, row)
	return c != nil, err
}

// WaitingFor: is anything waiting to be told to agent - its effective head, an answer to something it asked, a say (never a head, but
// still owed a delivery with nothing else queued), or a pending message still unsent. A closed agent is woken for that and nothing else.
func (m *Manager) WaitingFor(agent string) (yes bool, err error) {
	defer catch(&err)
	return m.waitingFor(agent), nil
}

// RecordOwedKey is the cursor a handoff's close sets: the agent's work went into the record, and the session that carried it is gone.
func RecordOwedKey(agent string) string { return "record_owed:" + agent }

// recordDue: a handoff closed this agent's session and its park has run out, so the wake is owed whatever else waits - the fresh session
// is handed the record, as the in-place wake of a cleared terminal session was.
func (m *Manager) recordDue(agent string) bool {
	if must(db.Cursor(m.Q, RecordOwedKey(agent))) == 0 {
		return false
	}
	row := must(db.GetAgentState(m.Q, agent))
	return row != nil && row.WakeAt.Valid && row.WakeAt.Int64 != 0 && row.WakeAt.Int64 <= m.now()
}

func (m *Manager) waitingFor(agent string) bool {
	return m.recordDue(agent) || must(m.Delivery.EffectiveHead(m.Q, agent)) != nil || len(must(db.UnannouncedAnswers(m.Q, agent))) > 0 ||
		len(must(db.SendNowPending(m.Q, agent))) > 0 || len(must(db.ListPending(m.Q, agent))) > 0
}

// coldBeforeTyping closes an open session whose cache has expired instead of typing into it: a line typed into it makes the vendor
// re-read the whole transcript uncached, and closing lets the wake, which starts a cold agent fresh on its handoff, deliver the same
// line. Only when there is something to type. closed is false with tried false when the session is warm or there is nothing to say.
func (m *Manager) ColdBeforeTyping(agent string, row *db.AgentState, now int64, st SessionStatus) (tried, closed bool, err error) {
	defer catch(&err)
	if must(m.Delivery.BgHolds(m.Q, agent, now)) || !m.cacheExpiredAt(agent, st.SessionID, now) {
		return false, false, nil
	}
	if now-must(db.Cursor(m.Q, "cold_close_try:"+agent)) < states.IdleCloseS {
		return false, false, nil // a close just failed: type as usual, not held forever
	}
	head := must(m.Delivery.EffectiveHead(m.Q, agent))
	told := head != nil && must(db.Cursor(m.Q, "notify:"+agent)) == head.ID && !must(m.Delivery.SessionChanged(m.Q, agent))
	found := must(m.Delivery.ForgottenResolve(m.Q, agent, row, now))
	nudge := found != nil && found.IdleS >= supervisor.NudgeAfterS && must(db.Cursor(m.Q, "nudge:"+agent)) != found.Head.ID
	if !(len(must(db.UnannouncedAnswers(m.Q, agent))) > 0 || (head != nil && !told) || nudge) {
		return false, false, nil
	}
	ok, _, err := m.CloseAgent(agent, st, CloseOpts{})
	if err != nil {
		return true, false, err
	}
	if !ok {
		check(db.SetCursor(m.Q, "cold_close_try:"+agent, now))
	}
	if ok && nudge {
		// The wake re-tells the head, which is the reminder; closing again for the same head would be one fresh turn an hour.
		check(db.SetCursor(m.Q, "nudge:"+agent, found.Head.ID))
	}
	return true, ok, nil
}

// noteAbandoned says why the work behind this agent's prompt is being stopped, once a stretch: the close that stops it is the only
// place this is known, since the session is marked closed, and the work gone, before the next sweep looks.
func (m *Manager) noteAbandoned(agent string, now int64) {
	if must(db.Cursor(m.Q, "bg_stop_noted:"+agent)) != 0 {
		return
	}
	check(db.SetCursor(m.Q, "bg_stop_noted:"+agent, now))
	grace := m.Settings.BgGraceS(agent)
	prompted := must(m.Delivery.BgPromptedAt(m.Q, agent))
	silent, _ := must2(m.Delivery.BgSilentS(m.Q, agent, now))
	must(db.AddEvent(m.Q, agent, "background_abandoned",
		db.KV{K: "prompted_s", V: now - prompted}, db.KV{K: "silent_s", V: silent},
		db.KV{K: "why", V: "background work still running " + timefmt.Ago(grace) + " after the agent was told to finish or stop it, with nothing shown"}))
}

// CloseAgent gives the machine back an idle agent's memory, keeping its terminal. This is not a handoff: no context is reclaimed,
// because nothing is wrong with this agent's context - it is simply not being used. The session id is recorded so waking can resume
// that same session, and so is a handoff record. The id is the caller's reading, the one ShouldClose approved: reading it again here
// would let the predicate check one value and the act store another, in the one place where the difference means closing with no way
// back.
func (m *Manager) CloseAgent(agent string, st SessionStatus, o CloseOpts) (ok bool, said string, err error) {
	defer catch(&err)
	if m.structured(agent) {
		defer m.lockAgent(agent)()
		if m.Run.Busy(agent) && !o.Force {
			return false, agent + " is mid-turn; not closing a session mid-work", nil
		}
		ok, said = m.closeAgent(agent, st, o)
		return ok, said, nil
	}
	release, mine := m.Typing.TakeTerminal(agent)
	if !mine {
		return false, TypingElsewhere + "; not closing", nil
	}
	defer release()
	ok, said = m.closeAgent(agent, st, o)
	return ok, said, nil
}

// CloseFresh ends this agent's session, if one runs, and forgets the one it would resume: its next wake is a new conversation that
// starts from the handoff record. A session already closed keeps its closed mark; only the id is dropped.
func (m *Manager) CloseFresh(agent string, st SessionStatus, o CloseOpts) (ok bool, said string, err error) {
	defer catch(&err)
	row := must(db.GetAgentState(m.Q, agent))
	if row == nil {
		return false, agent + " has never been started", nil
	}
	closed := row.ClosedAt.Valid && row.ClosedAt.Int64 != 0
	if !closed {
		o.Discard = true
		ok, said, err = m.CloseAgent(agent, st, o)
		if err != nil || !ok {
			return false, said, err
		}
	} else if row.ResumeSession.String != "" && must(db.GetHandoff(m.Q, agent)) == "" {
		// The session id was the only copy of its context; the record is what a new start is given.
		if gaps := must(m.Handoff.Record(m.Q, agent, row.ResumeSession.String)); len(gaps) > 0 {
			m.Log("  %s: fresh start with no handoff (could not read %s)", agent, strings.Join(gaps, ", "))
		}
	}
	check(db.ForgetResume(m.Q, agent))
	was := row.ResumeSession.String
	if was == "" {
		was = st.SessionID
	}
	var sv any
	if was != "" {
		sv = was
	}
	must(db.AddEvent(m.Q, agent, "fresh_close", db.KV{K: "session", V: sv}))
	return true, agent + ": closed; its next wake starts a new session, not the old conversation", nil
}

// restartOwed is what a restart that closed this agent still owes it - {by, fresh, cause} - else nil: a level read from the record, so a
// restart whose wake failed is tried again by every tick until a session is up, rather than left to someone noticing a bare shell. Over
// once a wake is recorded after it, or the agent was closed again since.
func (m *Manager) restartOwed(agent string, row *db.AgentState) map[string]any {
	if row == nil || !(row.ClosedAt.Valid && row.ClosedAt.Int64 != 0) {
		return nil
	}
	id, payload, ok := must3(db.LastEvent(m.Q, agent, RestartOwedKind))
	if !ok {
		return nil
	}
	if woke, ok := must2(db.LastEventID(m.Q, agent, "wake")); ok && woke > id {
		return nil
	}
	if closed, ok := must2(db.LastEventID(m.Q, agent, "close")); ok && closed > id {
		return nil
	}
	return payload
}

// moveOwed is the account a person moved this closed agent to - {to, by} - else nil: owed to the wake that ends this close. Over once a
// wake is recorded after it, or once the agent was closed again since: some starts record no wake (a trust screen, a hand relaunch), and
// their close is.
func (m *Manager) moveOwed(agent string, row *db.AgentState) map[string]any {
	if row == nil || !(row.ClosedAt.Valid && row.ClosedAt.Int64 != 0) {
		return nil
	}
	id, payload, ok := must3(db.LastEvent(m.Q, agent, MoveOwedKind))
	if !ok {
		return nil
	}
	if at, _ := must2(db.LastEventTS(m.Q, agent, MoveOwedKind)); row.ClosedAt.Int64 > at {
		return nil
	}
	if woke, ok := must2(db.LastEventID(m.Q, agent, "wake")); ok && woke > id {
		return nil
	}
	return payload
}

func must3[A, B, C any](a A, b B, c C, err error) (A, B, C) {
	check(err)
	return a, b, c
}

// RestartOwed is restartOwed, for the callers outside.
func (m *Manager) RestartOwed(agent string, row *db.AgentState) (owed map[string]any, err error) {
	defer catch(&err)
	return m.restartOwed(agent, row), nil
}

// RestartRetryWait is the seconds before the next try of a restart owed, after wakes that failed: 0 when none has.
func (m *Manager) RestartRetryWait(agent string, row *db.AgentState, now int64) (wait int64, err error) {
	defer catch(&err)
	if row == nil || row.WakeFailures == 0 {
		return 0, nil
	}
	last, ok := must2(db.LastEventTS(m.Q, agent, "wake_failed"))
	if !ok {
		return 0, nil
	}
	i := int(row.WakeFailures)
	if i > len(WakeBackoffS) {
		i = len(WakeBackoffS)
	}
	return max(0, last+WakeBackoffS[i-1]-now), nil
}

// RestartRetryNote is what a screen says beside a closed agent whose restart is owed: that it is under way, or that it is down, why, and
// which try is next. "" when no restart is owed.
func (m *Manager) RestartRetryNote(agent string, row *db.AgentState) (note string, err error) {
	defer catch(&err)
	if m.restartOwed(agent, row) == nil {
		return "", nil
	}
	failed := row.WakeFailures
	id, payload, ok := must3(db.LastEvent(m.Q, agent, "wake_failed"))
	owedID, _ := must2(db.LastEventID(m.Q, agent, RestartOwedKind))
	if !ok || id < owedID {
		return "restarting", nil
	}
	why, _ := payload["why"].(string)
	if why == "" {
		why = "it did not start"
	}
	if failed != 0 {
		return fmt.Sprintf("down, retry %d/%d: %s", failed+1, states.MaxFailedAttempts, why), nil
	}
	return fmt.Sprintf("down: %s, retrying", why), nil
}

// RestartOpts say how a restart goes.
type RestartOpts struct {
	Fresh, Force bool
	By           string
	Applying     []string
	Cause        string
}

// RestartAgent ends this agent's session and starts it again at once, in the same terminal: the close and the wake, nothing of its own.
// By default it resumes the same conversation, whatever its prompt cache says, so it picks up what a launch reads; Fresh starts a new
// one from the handoff record. A working agent is refused unless Force, which closes it as any close does (the handoff record first,
// /exit once its turn has ended) and so waits for that turn. Work queued for it is the resume's first prompt, after the line that says it
// was restarted, by By.
func (m *Manager) RestartAgent(agent string, st SessionStatus, o RestartOpts) (ok bool, said string, err error) {
	defer catch(&err)
	if o.By == "" {
		o.By = db.Human
	}
	if o.Cause == "" {
		o.Cause = "restart"
	}
	row := must(db.GetAgentState(m.Q, agent))
	if row == nil {
		return false, "it has never been started", nil
	}
	live := !(row.ClosedAt.Valid && row.ClosedAt.Int64 != 0)
	if live && row.State == states.Handoff {
		return false, "it is mid-handoff", nil
	}
	if live && row.State == states.Running && !o.Force {
		return false, "it is mid-turn (--force waits for the turn to end first)", nil
	}
	paneBefore, _ := must2(db.LastEventID(m.Q, agent, "terminal_closed"))
	byPane := false
	if live || o.Fresh {
		// A restart asked for by name is not left standing at a screen that cannot be read: past every other guard of the close, its
		// terminal is closed with the session in it.
		c := CloseOpts{KeepPane: true, Force: o.Force, PaneMayClose: true}
		if o.Fresh {
			ok, said, err = m.CloseFresh(agent, st, c)
		} else {
			ok, said, err = m.CloseAgent(agent, st, c)
		}
		if err != nil || !ok {
			return false, said, err
		}
		after, _ := must2(db.LastEventID(m.Q, agent, "terminal_closed"))
		byPane = after != paneBefore
	}
	must(db.AddEvent(m.Q, agent, RestartOwedKind, db.KV{K: "by", V: o.By}, db.KV{K: "fresh", V: o.Fresh}, db.KV{K: "cause", V: o.Cause}))
	ok, why, err := m.WakeAgent(agent, WakeOpts{Force: true, Warm: !o.Fresh, Cause: o.Cause, RestartedBy: o.By, Applying: o.Applying})
	if err != nil {
		return false, "", err
	}
	if !ok {
		return false, "it was closed but not started again - " + why, nil
	}
	note := ""
	if byPane {
		note = "; its screen could not be read, so its terminal was closed with it"
	}
	how := ", resuming its conversation"
	if o.Fresh {
		how = " as a new conversation"
	}
	return true, agent + ": restarted" + how + note, nil
}

func (m *Manager) closeAgent(agent string, st SessionStatus, o CloseOpts) (bool, string) {
	row := must(db.GetAgentState(m.Q, agent))
	if row != nil && (row.State == states.Running || row.State == states.Handoff) && !(o.Force && row.State == states.Running) {
		// ShouldClose refuses these, and every path that closes on a schedule goes through it - but a human's key does not, and a turn
		// can start between the moment they press it and the moment they confirm. Closing then throws away work nothing has recorded.
		// A handoff is the exception and calls endSession itself: it is the one path that has already recorded the work, and then needs
		// the session gone to start the next one somewhere else. Force is a restart's own call for a working agent.
		return false, fmt.Sprintf("%s is %s; not closing a session mid-work", agent, row.State)
	}
	if row != nil && row.State == states.Running {
		// Only a forced restart gets here: its handoff record is taken below, after the turn, so the turn's last work is in it.
		if !m.waitIdle(agent, must(db.Terminal(m.Q, agent))) {
			return false, "it is still mid-turn after waiting for it to finish; not closing"
		}
	}
	if st.SessionID == "" && !o.Discard {
		// Closing is only cheap because waking resumes the same session. With no id to come back to, closing is just throwing the context
		// away - which --fresh asks for, and is the one way out of a live session whose id was never recorded.
		return false, "no session id on record; not closing (`hl restart --fresh` starts a new conversation from its handoff)"
	}
	// A record already waiting is the park's, and names the session from before its /clear: this close must neither replace it nor, if
	// it fails, delete it. One it takes itself is kept only whole - a wake that moves elsewhere drops the resume id, the only complete
	// copy of the context.
	wrote := false
	if must(db.GetHandoff(m.Q, agent)) == "" {
		text, missing := must2(m.Handoff.Capture(m.Q, agent))
		if len(missing) == 0 {
			check(db.SetHandoff(m.Q, agent, text))
			wrote = true
		}
	}
	ok, why := m.endSession(agent, st.SessionID, st, o.KeepPane, o.PaneMayClose)
	if !ok && wrote {
		check(db.ClearHandoff(m.Q, agent))
	}
	if ok {
		m.Handoff.ClearNote(m.Q, agent)
	}
	return ok, why
}

// answerExitDialog: the /exit just typed can raise a dialog offering to stop background work: decline it (Esc keeps the session). Only
// with stop - the work is stale and its supervisor has had its say - is it answered the other way, and then only by the narrow match; a
// dialog that does not match is declined as ever. True when one was declined; a dialog that would not be, or none, is left to the
// session-still-running report. Stops when the session is gone, or after the wait.
func (m *Manager) answerExitDialog(agent string, stop bool) bool {
	wait := time.Duration(min(supervisor.ExitDialogWaitS, supervisor.EndWaitS)) * time.Second
	deadline := time.Now().Add(wait)
	handle := must(db.Terminal(m.Q, agent))
	for time.Now().Before(deadline) {
		if sessions, ok := m.Observe.LiveSessions(); ok && sessions[agent] == 0 {
			return false
		}
		var seen *term.Blocker
		if handle != "" {
			seen = m.Term.ScreenBlocker(handle)
		}
		if seen != nil && seen.Kind == term.Modal {
			if stop && m.Blocked.StopWorkAndExit(m.Q, agent) {
				return false
			}
			ok, _ := m.Blocked.PastDialog(m.Q, agent)
			return !ok
		}
		m.sleep(supervisor.EndPollS * time.Second)
	}
	return false
}

// endSession types /exit at this agent, and records what may bring it back. Once the session is gone its pane is closed and the handle
// dropped: the wake opens a new terminal in the same worktree, so an idle agent holds no window. keepPane is for the callers that start
// a session in it again at once, and an agent that never closes keeps its pane. paneMayClose lets a screen that cannot be read be
// answered by closing the terminal with the session in it, once every guard has passed: /exit is never typed at a screen nobody can
// read. resume is the session id to come back to, "" when nothing may: a session id exists only under its own account's config
// directory, so a close that precedes a move to another account must record none.
func (m *Manager) endSession(agent, resume string, st SessionStatus, keepPane, paneMayClose bool) (bool, string) {
	if m.structured(agent) {
		return m.endStructured(agent, resume, st)
	}
	handle := must(db.Terminal(m.Q, agent))
	if handle == "" || !m.Term.Available() {
		return false, "no terminal to close"
	}
	if present := must(m.Delivery.SessionPresent(agent)); !present {
		// /exit typed at the shell exits the shell, and the terminal goes with it - so the one path that turns a recoverable state into
		// an unrecoverable one is closing something that already closed itself.
		return false, "no session process; nothing to close"
	}
	// Work the session says it still runs is not ours to stop: an agent with background work is not idle, whichever path is closing it -
	// unless it is stale and its supervisor was told.
	now := m.now()
	stale := must(m.Delivery.BgMayStop(m.Q, agent, now))
	running, _ := m.Term.BackgroundWork(handle)
	if running != "" && !stale {
		return false, fmt.Sprintf("background work is running (%s); not closing", running)
	}
	if running != "" && must(m.Delivery.BgOwesNothing(m.Q, agent)) {
		m.noteAbandoned(agent, now)
	}
	// A dialog on screen is what /exit would answer instead of ending anything. Past it first, so the close is one act and not a refusal
	// the next tick meets again; when it cannot be, the refusal says why.
	stuck := must(m.Delivery.PastDialog(m.Q, agent))
	ok, why := m.Term.CloseSession(handle)
	paneGone := false
	if !ok && paneMayClose {
		if seen := m.Term.ScreenBlocker(handle); seen != nil && seen.Kind == term.Unreadable {
			ok, why = m.Term.CloseTerminal(handle)
			paneGone = ok
			if ok {
				// The handle points at nothing now, whether or not the session ends with it.
				row := must(db.GetAgentState(m.Q, agent))
				wt := ""
				if row != nil {
					wt = row.Worktree.String
				}
				check(db.SetTerminal(m.Q, agent, "", wt))
				must(db.AddEvent(m.Q, agent, "terminal_closed", db.KV{K: "handle", V: handle}))
			} else {
				why = "its screen could not be read and its terminal would not close: " + why
			}
		}
	}
	if !ok && stuck != "" {
		why = fmt.Sprintf("%s (%s)", why, stuck)
	}
	if !ok {
		// Refused outright - most often a dialog already on screen - is ocp's own doing exactly as much as a /exit that was typed and
		// never landed, so it is on the list the same way: at once, not on the blocked sweep's own next pass over this agent.
		m.Blocked.NoteCloseFailure(m.Q, agent)
		return false, why
	}
	// A dialog that offers to stop background work only appears now; declining it is the answer, so this is a refusal, not a failed close.
	if !paneGone && m.answerExitDialog(agent, stale) {
		why = "background work is running; the exit dialog was declined, session kept"
		must(db.AddEvent(m.Q, agent, "close_failed", db.KV{K: "why", V: why}, db.KV{K: "pid", V: nil}))
		return false, why
	}
	// Typed is not ended: the /exit can be eaten or slow, and a closed row over a live process is cleared by the sweep, while a relaunch
	// typed at that terminal reaches the old session as a prompt. A session that does not end is reported, neither retried (a second
	// /exit goes to whatever ate the first) nor killed (that skips its own shutdown); the sweep records the close if it ends later.
	gone := m.Delivery.WaitSessionGone(agent)
	onScreen := gone && !paneGone && m.Delivery.SessionStillDrawn(handle, true)
	if !gone || onScreen {
		sessions, _ := m.Observe.LiveSessions()
		pid := sessions[agent]
		typed := "/exit was typed"
		if paneGone {
			typed = "its terminal was closed"
		}
		var pv any
		if !gone {
			if pid != 0 {
				why = fmt.Sprintf("%s but the session is still running (pid %d) after %ds; not marked closed", typed, pid, supervisor.EndWaitS)
			} else {
				why = fmt.Sprintf("%s but the session is still running after %ds; not marked closed", typed, supervisor.EndWaitS)
			}
		} else {
			why = "/exit was typed and the process table lists no session, but one is still drawn on the terminal's screen; not marked closed"
		}
		if pid != 0 {
			pv = pid
		}
		must(db.AddEvent(m.Q, agent, "close_failed", db.KV{K: "why", V: why}, db.KV{K: "pid", V: pv}))
		m.Blocked.NoteCloseFailure(m.Q, agent)
		return false, why
	}
	check(db.SetClosed(m.Q, agent, resume))
	// RUNNING describes a session that exists. Leaving the label on a closed one makes every screen say the opposite of what happened,
	// and the daemon skips RUNNING agents when it decides what to close next.
	if row := must(db.GetAgentState(m.Q, agent)); row != nil && row.State == states.Running {
		check(db.SetAgentState(m.Q, agent, states.Idle, row.WakeAt.Int64, "session closed while idle"))
	}
	var sv any
	if st.SessionID != "" {
		sv = st.SessionID
	}
	must(db.AddEvent(m.Q, agent, "close", db.KV{K: "session", V: sv}, db.KV{K: "ctx_pct", V: st.CtxPct}))
	m.Log("  %s: session closed (resume %s)", agent, orUnknown(st.SessionID))
	if paneGone {
		m.Log("  %s: its screen could not be read; terminal closed with the session", agent)
		return true, agent + ": session ended, " + PaneMark
	}
	if keepPane || m.Settings.CloseTimeoutS(agent) == Never {
		return true, agent + ": session closed, terminal kept"
	}
	return true, agent + ": session closed, " + m.closePane(agent, handle)
}

// EndSession is endSession for a handoff, which has already recorded the work and needs the session gone.
func (m *Manager) EndSession(agent, resume string, st SessionStatus, keepPane, paneMayClose bool) (ok bool, why string, err error) {
	defer catch(&err)
	if m.structured(agent) {
		defer m.lockAgent(agent)()
	}
	ok, why = m.endSession(agent, resume, st, keepPane, paneMayClose)
	return ok, why, nil
}

// closePane closes the terminal a closed session left behind. The handle is dropped only once the host has closed the pane, and the
// worktree stays on the row, where the wake starts it again. A pane that stays is only a shell: the next wake types into it as before,
// and the sweep drops the handle if it is gone anyway.
func (m *Manager) closePane(agent, handle string) string {
	ok, why := m.Term.CloseTerminal(handle)
	if !ok {
		m.Log("  %s: session closed but its terminal was not: %s", agent, why)
		return "terminal kept (" + why + ")"
	}
	wt := ""
	if row := must(db.GetAgentState(m.Q, agent)); row != nil {
		wt = row.Worktree.String
	}
	check(db.SetTerminal(m.Q, agent, "", wt))
	must(db.AddEvent(m.Q, agent, "terminal_closed", db.KV{K: "handle", V: handle}))
	m.Log("  %s: terminal closed", agent)
	return "terminal closed"
}
