package lifecycle

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/timefmt"
)

// The triggers a move answers (daemon/handoff.py): the two walls, a person's order, and a login that expired.
const (
	LoginTrigger  = "login"
	ClearWaitS    = 300
	handoffReason = "handoff"
)

// MovableTrigger is handoff.MOVABLE_TRIGGERS.
func MovableTrigger(kind string) bool {
	return kind == "usage" || kind == "usage_7d" || kind == "forced" || kind == LoginTrigger
}

func usageTrigger(kind string) bool { return kind == "usage" || kind == "usage_7d" }

// HandoffTrigger is why a handoff happens: its kind and the sentence for it.
type HandoffTrigger struct{ Kind, Reason string }

// Windows are an account's two windows as the move reads them.
type Windows struct {
	ShortReset, LongReset int64
	ShortS                float64 // the shortest window's length in seconds
}

// MoveEnv is what a handoff and a move read beyond the lifecycle: usage, the note, and the policy's pure functions.
type MoveEnv struct {
	// Reading is agents.account_reading: the account and how it is known ("recorded", "reported" or "assumed").
	Reading func(q db.Q, agent string) (account, source string, ok bool)
	Windows func(acct string) Windows
	// ColdStartPct is measure.cold_start_pct, nil when nothing was measured.
	ColdStartPct func(q db.Q) *float64
	// AtLimit is when the agent's last turn ended on a usage limit, ok false when it did not.
	AtLimit func(q db.Q, agent string) (at int64, ok bool)
	// NoteHold is handoff_note.hold: the ask is out and the note not yet in.
	NoteHold func(q db.Q, agent string, now int64) bool
	// BackgroundWork is what the agent's session still runs behind its prompt, "" for nothing.
	BackgroundWork func(q db.Q, agent string) string
	// ContextUse is the context used and its limit, used < 0 when unknown.
	ContextUse func(q db.Q, agent string) (used, limit int)
	// RunningAccount is the account the agent's process spends, "" when unknown (never a contradiction).
	RunningAccount func(agent string) string
	MeasureForget  func(q db.Q, agent string)
	// The policy's pure functions (tick.StayWithin, StayOrMove, WakeAfter), passed in so lifecycle does not import the tick.
	StayWithin func(coldStartPct *float64, windowS float64) float64
	StayOrMove func(workWaiting bool, resetInS *float64, stayWithin float64) (bool, string)
	WakeAfter  func(kind string, shortReset, longReset, now int64) int64
}

// Mover is daemon/handoff.py for structured sessions: the handoff, the move to another account and the clear that is owed. When each
// happens is Python's; how is not: nothing is typed, a close is proven by the process group, and a cleared context is a closed session
// whose next start is fresh from the record.
type Mover struct {
	M *Manager
	E MoveEnv
}

func (v *Mover) log(f string, a ...any) { v.M.Log(f, a...) }

// recordHandoff takes the record and commits the step. False when a fact it holds could not be read.
func (v *Mover) recordHandoff(agent, hid string) bool {
	m := v.M
	missing := must(m.Handoff.Record(m.Q, agent, ""))
	if len(missing) == 0 {
		check(db.AdvanceHandoff(m.Q, hid, "recorded", "in_flight", ""))
		return true
	}
	why := "could not read " + strings.Join(missing, ", ")
	check(db.AdvanceHandoff(m.Q, hid, "recorded", "degraded", clip300(why)))
	check(db.BumpCounter(m.Q, agent, "throttle_count", 1))
	v.log("  %s: handoff degraded (%s)", agent, why)
	return false
}

func (v *Mover) lastLeft(agent string) string {
	var payload string
	if err := v.M.Q.QueryRow("SELECT payload FROM events WHERE agent = ? AND kind = 'moved_account' ORDER BY id DESC LIMIT 1", agent).Scan(&payload); err != nil {
		return ""
	}
	var p struct {
		From string `json:"from"`
	}
	if json.Unmarshal([]byte(payload), &p) != nil {
		return ""
	}
	return p.From
}

// MovePlan is an agent at the wall: stay, or move and where to. (destination, why). Stay when nothing waits for it or its own account resets
// before a cold start would pay back; otherwise the room test the closed-agent wake already uses (Launcher.full over the other accounts),
// never back to the account it just left before that resets, so one wall is one move.
func (v *Mover) MovePlan(agent, kind string) (dest, why string, err error) {
	defer catch(&err)
	m := v.M
	here, _, ok := v.E.Reading(m.Q, agent)
	if !ok || here == "" {
		return "", "no account is recorded for it", nil
	}
	now := m.now()
	w := v.E.Windows(here)
	reset := w.ShortReset
	if kind == "usage_7d" {
		reset = w.LongReset
	}
	var resetIn *float64
	if reset != 0 {
		d := float64(max(0, reset-now))
		resetIn = &d
	}
	work := len(must(db.QueuedFor(m.Q, agent))) > 0 || len(must(db.UnannouncedAnswers(m.Q, agent))) > 0
	if kind != LoginTrigger {
		if move, why := v.E.StayOrMove(work, resetIn, v.E.StayWithin(v.E.ColdStartPct(m.Q), w.ShortS)); !move {
			return "", why, nil
		}
	}
	exclude := map[string]string{here: "the account it is at the wall on"}
	if left := v.lastLeft(agent); left != "" && left != here {
		if r := v.E.Windows(left).ShortReset; r > now {
			exclude[left] = "the account it just left, until it resets"
		}
	}
	row := must(db.GetAgentState(m.Q, agent))
	dest, why = m.Launch.RoomElsewhere(m.Q, agent, row, exclude)
	if dest == "" {
		return "", why, nil
	}
	return dest, "it has room", nil
}

// forcedPlan holds a person's chosen destination to the same room test.
func (v *Mover) forcedPlan(agent, dest string) (string, string) {
	m := v.M
	row := must(db.GetAgentState(m.Q, agent))
	if why := m.Launch.RoomOn(m.Q, agent, row, dest); why != "" {
		return "", dest + " " + why
	}
	return dest, "it has room"
}

// closeFresh ends the session with no resume id: a session id lives under one account's config directory, and a cleared context is a
// session that is not resumed. Close errors while any process of the group lives, and that is the proof.
func (v *Mover) closeFresh(agent string) (bool, string) {
	m := v.M
	here := m.Agents.CurrentAccount(m.Q, agent)
	st := m.Status.ReadForAgent(agent, here)
	ok, why := m.endStructured(agent, "", st)
	if ok {
		check(db.ForgetResume(m.Q, agent))
		m.Handoff.ClearNote(m.Q, agent)
	}
	return ok, why
}

// moveToRoom puts the agent's next session on an account that has room. (moved, retrySoon); retrySoon is meaningful only when not moved:
// a destination existed, so what stopped the move is about this session now and not about the accounts.
func (v *Mover) moveToRoom(agent string, t HandoffTrigger, dest string) (moved, retrySoon bool) {
	m := v.M
	if !MovableTrigger(t.Kind) {
		return false, false
	}
	here := m.Agents.CurrentAccount(m.Q, agent)
	var there, why string
	if dest != "" {
		there, why = v.forcedPlan(agent, dest)
	} else {
		var err error
		there, why, err = v.MovePlan(agent, t.Kind)
		check(err)
	}
	if there == "" {
		v.log("  %s: staying on %s - %s", agent, here, why)
		return false, false
	}
	if !m.Run.Alive(agent) {
		return false, true
	}
	if ok, why := v.closeFresh(agent); !ok {
		v.log("  %s: staying on %s - %s", agent, here, why)
		return false, true
	}
	// HANDOFF protects the session that just ended; it comes off before the wake so the new session's own first turn can set RUNNING.
	check(db.SetAgentState(m.Q, agent, states.Queued, 0, fmt.Sprintf("moving %s -> %s", here, there)))
	m.Agents.MeasureBegin(m.Q, agent, there)
	woke, wakeWhy := m.wakeAgent(agent, WakeOpts{MovedTo: there})
	landed := must(db.GetAgentState(m.Q, agent))
	if !woke && landed != nil && landed.State != states.Running && isClosed(landed) {
		// Nothing started: the row still says closed, and parking it with a deadline is what brings a closed agent back.
		v.E.MeasureForget(m.Q, agent)
		v.log("  %s: closed on %s but could not start on %s: %s", agent, here, there, clip(wakeWhy, 120))
		return false, true
	}
	// The session that started must be the one on `there`; unknown is not a contradiction.
	if actual := v.E.RunningAccount(agent); actual != "" && actual != there {
		v.E.MeasureForget(m.Q, agent)
		v.log("  %s: asked to move %s -> %s but its process is on %s - not recorded as moved", agent, here, there, actual)
		return false, true
	}
	check(db.SetAccount(m.Q, agent, there, ""))
	hookRunning := !must(db.SetStateUnlessRunning(m.Q, agent, states.Queued, fmt.Sprintf("moved %s -> %s (%s)", here, there, t.Kind)))
	if hookRunning {
		if fresh := must(db.GetAgentState(m.Q, agent)); fresh != nil && isClosed(fresh) {
			check(db.ClearClosed(m.Q, agent))
		}
		if !woke {
			check(db.ResetCounter(m.Q, agent, "wake_failures"))
			check(db.ClearHandoff(m.Q, agent))
		}
	}
	must(db.AddEvent(m.Q, agent, "moved_account", db.KV{K: "from", V: here}, db.KV{K: "to", V: there}, db.KV{K: "trigger", V: t.Kind}))
	v.log("  %s: moved %s -> %s (%s) - %s%s", agent, here, there, t.Kind, why, ifTrue(!woke && !hookRunning, " - but it was not handed its handoff: "+clip(wakeWhy, 80)))
	return true, false
}

func clip(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[:n])
	}
	return s
}

// MoveToRoom is the move for a caller that has the agent's lock already decided (the tick's un-park of a walled agent).
func (v *Mover) MoveToRoom(agent string, t HandoffTrigger) (moved bool, err error) {
	defer catch(&err)
	defer v.M.lockAgent(agent)()
	if v.M.Run.Busy(agent) {
		return false, nil
	}
	moved, _ = v.moveToRoom(agent, t, "")
	return moved, nil
}

// PerformHandoff runs the pipeline, committing each step. True when the agent ended parked cleanly or the handoff was deferred (a defer
// counts nothing against it: the trigger is a condition and fires again), false when it degraded.
func (v *Mover) PerformHandoff(agent string, t HandoffTrigger, dest string) (ok bool, err error) {
	defer catch(&err)
	m := v.M
	defer m.lockAgent(agent)()
	if existing := must(db.InFlightFor(m.Q, agent)); existing != nil {
		check(db.AdvanceHandoff(m.Q, existing.ID, existing.Step, existing.State, strings.Trim(existing.Detail.String+"; also "+t.Kind, "; ")))
		v.log("  %s: already handing off at step '%s', merged '%s' as an added reason", agent, existing.Step, t.Kind)
		return true, nil
	}
	now := m.now()
	var last *int64
	_ = m.Q.QueryRow("SELECT MAX(updated) FROM handoff WHERE agent = ?", agent).Scan(&last)
	if last != nil && t.Kind != "forced" && t.Kind != LoginTrigger && now-*last < states.MinHandoffIntervalS {
		v.log("  %s: %s fired again %ds after the last handoff - too soon, skipping", agent, t.Kind, now-*last)
		return true, nil
	}
	if !m.Run.Alive(agent) {
		v.log("  %s: %s fired but no session is running - not handing off", agent, t.Kind)
		return true, nil
	}
	// A turn that ended on the usage limit and has stayed quiet is over, whatever else says; one that ended on an expired login is over at
	// once. Sooner than that a person or the agent may be mid-turn.
	stoppedAt, stopped := v.E.AtLimit(m.Q, agent)
	settled := t.Kind == LoginTrigger || (stopped && now-stoppedAt >= states.LimitSettleS)
	if !settled && m.Run.Busy(agent) {
		if t.Kind != "forced" {
			v.log("  %s: %s fired but the agent is mid-turn - deferring", agent, t.Kind)
			return true, nil
		}
		// A person's order does not wait for a turn that may never end on its own, an open permission prompt included: the interrupt
		// denies it, and the act goes on once the turn is over.
		check(m.Run.Interrupt(agent))
		if !m.Run.WaitIdle(agent, StructuredIdleWait) {
			v.log("  %s: forced move fired but the turn did not end - not handing off", agent)
			return true, nil
		}
	}
	// The agent writes its own part of the record first, one turn while its cache is warm. A person's order is not held for it, and a
	// session at the wall cannot take a turn.
	if t.Kind != "forced" && t.Kind != LoginTrigger && !settled && v.E.NoteHold(m.Q, agent, now) {
		v.log("  %s: %s fired - waiting for its handoff note", agent, t.Kind)
		return true, nil
	}
	return v.perform(agent, t, dest), nil
}

func (v *Mover) perform(agent string, t HandoffTrigger, dest string) bool {
	m := v.M
	hid := newHandoffID()
	acct := m.Agents.CurrentAccount(m.Q, agent)
	st := m.Status.ReadForAgent(agent, acct)
	check(db.StartHandoff(m.Q, hid, agent, st.SessionID, t.Kind, m.now()+states.HandoffDeadlineS))
	check(db.SetAgentState(m.Q, agent, states.Handoff, 0, t.Reason))
	v.log("  %s: HANDOFF (%s) - %s", agent, t.Kind, t.Reason)

	prior := must(db.GetHandoff(m.Q, agent))
	ok := v.recordHandoff(agent, hid)
	check(db.AdvanceHandoff(m.Q, hid, "extracted", "in_flight", ""))
	released := func(detail string) {
		check(db.AdvanceHandoff(m.Q, hid, "released", ifTrue(ok, "done")+ifTrue(!ok, "degraded"), detail))
	}

	moved, retrySoon := v.moveToRoom(agent, t, dest)
	if moved {
		released("")
		check(db.BumpCounter(m.Q, agent, "continuations", 1))
		if ok {
			check(db.ResetCounter(m.Q, agent, "throttle_count"))
		}
		return ok
	}
	if landed := must(db.GetAgentState(m.Q, agent)); t.Kind == "forced" && landed != nil && !isClosed(landed) {
		// Nothing was ended, so there is nothing to park. The record this attempt took would be injected at the next start as
		// "you were interrupted"; the row goes back as it was.
		if prior == "" {
			check(db.ClearHandoff(m.Q, agent))
		} else {
			check(db.SetHandoff(m.Q, agent, prior))
		}
		check(db.AdvanceHandoff(m.Q, hid, "released", "degraded", "forced move did not happen"))
		check(db.SetAgentState(m.Q, agent, states.Idle, 0, "forced move did not happen"))
		return false
	}
	cleared, why := v.reclaim(agent)
	if !cleared && v.clearOwed(agent) != 0 {
		// Background work holds the close off and the agent was told: a wait, not a failure.
		released(why)
		check(db.SetAgentState(m.Q, agent, states.Idle, 0, "clear owed: "+why))
		v.log("  %s: handoff recorded; %s", agent, why)
		return ok
	}
	check(db.AdvanceHandoff(m.Q, hid, "extracted", "in_flight", why))
	if ok && cleared {
		check(db.ResetCounter(m.Q, agent, "throttle_count"))
	}
	v.park(agent, t, hid, ok, cleared, retrySoon)
	return ok
}

func (v *Mover) clearOwed(agent string) int64 { return must(db.Cursor(v.M.Q, "clear_owed:"+agent)) }

// ClearNotice is what the agent is told when its clear waits for background work (delivery.CLEAR_NOTICE).
const ClearNotice = "ocp: your context is full (%s), so this session will be cleared in %d minutes. No reply is needed. Before then: 1. Stop your background shells. Anything still running at that time is cut off. 2. If you were waiting on a result, write what was running and what is left in: hl progress \"done: ... left: ...\" - the next session starts from that note. 3. Do not start new work or new background jobs. When your background work has ended, the clear happens at once."

// reclaim returns the context: the session is closed with no resume id and the park's wake starts a fresh one from the record. Work the
// session still runs is cut off by the close, so it is owed: the agent is told once and the close comes when the work ends or its time is
// up.
func (v *Mover) reclaim(agent string) (bool, string) {
	m := v.M
	if !m.Run.Alive(agent) {
		return false, "no session process; nothing to close"
	}
	running := v.E.BackgroundWork(m.Q, agent)
	if running == "" {
		return v.closeNow(agent)
	}
	now := m.now()
	since := v.clearOwed(agent)
	if last := must2opt(db.LastActivity(m.Q, agent)); last != 0 && now-last >= states.PromptCacheS {
		v.log("  %s: background work is running (%s) but its cache has lapsed - closing", agent, running)
		return v.closeNow(agent)
	}
	owed := fmt.Sprintf("background work is running (%s); the close is owed", running)
	if since == 0 {
		used, limit := v.E.ContextUse(m.Q, agent)
		usedText := "?"
		if used >= 0 {
			usedText = fmt.Sprint(used)
		}
		err := m.Run.Send(agent, "clear_notice:"+fmt.Sprint(now), fmt.Sprintf(ClearNotice, usedText+" of "+fmt.Sprint(limit), ClearWaitS/60))
		check(db.SetCursor(m.Q, "clear_owed:"+agent, now)) // owed even if the notice did not go
		v.log("  %s: background work is running (%s); told it will be cleared in %d min%s", agent, running, ClearWaitS/60, ifTrue(err != nil, " (the notice failed)"))
		return false, owed
	}
	if now-since < ClearWaitS {
		return false, owed
	}
	v.log("  %s: %d min are up with background work still running (%s) - closing", agent, ClearWaitS/60, running)
	ok, why := v.closeNow(agent)
	if ok {
		rec := must(db.GetHandoff(m.Q, agent))
		check(db.SetHandoff(m.Q, agent, fmt.Sprintf("%s\n\n## Cut off by the clear\nThis session was cleared with background work still running (%s); it was told %s earlier and asked to stop it. That work is gone - rerun it if it still matters.\n", rec, running, timefmt.Ago(now-since))))
	}
	return ok, why
}

func must2opt(v int64, ok bool, err error) int64 {
	if err != nil || !ok {
		return 0
	}
	return v
}

func (v *Mover) closeNow(agent string) (bool, string) {
	ok, why := v.closeFresh(agent)
	if ok {
		check(db.ClearCursor(v.M.Q, "clear_owed:"+agent))
	}
	return ok, why
}

// SettleOwedClear is true while this agent's clear is owed: the close comes once its background work has ended or its time is up,
// and the agent is parked as the handoff would have. False when nothing is owed or the session it was owed for is gone.
func (v *Mover) SettleOwedClear(agent string) (owed bool, err error) {
	defer catch(&err)
	m := v.M
	if v.clearOwed(agent) == 0 {
		return false, nil
	}
	row := must(db.GetAgentState(m.Q, agent))
	if row == nil || isClosed(row) || !m.Run.Alive(agent) {
		check(db.ClearCursor(m.Q, "clear_owed:"+agent))
		return false, nil
	}
	defer m.lockAgent(agent)()
	if m.Run.Busy(agent) {
		return true, nil
	}
	cleared, why := v.reclaim(agent)
	if !cleared {
		v.log("  %s: its clear is owed and holds all it is told - %s", agent, ifTrue(why != "", why)+ifTrue(why == "", "the close did not go"))
		return true, nil
	}
	kind, ok := "context", false
	if h := must(db.LastHandoffTrigger(m.Q, agent)); h != "" {
		kind = h
	}
	var state string
	if err := m.Q.QueryRow("SELECT state FROM handoff WHERE agent = ? ORDER BY updated DESC LIMIT 1", agent).Scan(&state); err == nil {
		ok = state == "done"
	}
	if ok {
		check(db.ResetCounter(m.Q, agent, "throttle_count"))
	}
	v.park(agent, HandoffTrigger{Kind: kind, Reason: "clear owed"}, "", ok, true, false)
	return true, nil
}

// park is where a handoff that reached its close ends: parked until it may be woken. hid is "" for a close that was owed and came later.
func (v *Mover) park(agent string, t HandoffTrigger, hid string, ok, cleared, retrySoon bool) {
	m := v.M
	now := m.now()
	acct, source, have := v.E.Reading(m.Q, agent)
	shortRetry := !have || source == "assumed" || t.Kind == "forced" || retrySoon
	var wake int64
	switch {
	case shortRetry:
		wake = now + states.AssumedAccountRecheckS
	case usageTrigger(t.Kind) || t.Kind == states.ByHandMark:
		w := v.E.Windows(acct)
		wake = v.E.WakeAfter(t.Kind, w.ShortReset, w.LongReset, now)
	default:
		// Not a wall: the context is handled by the close, but the wake is still what hands the agent its record back.
		wake = now
		if !cleared {
			check(db.BumpCounter(m.Q, agent, "throttle_count", 1))
		}
	}
	label := t.Kind
	if shortRetry && t.Kind != states.ByHandMark {
		label = states.MovingMark
	}
	if fresh := must(db.GetAgentState(m.Q, agent)); fresh != nil && fresh.ThrottleCount > 0 {
		if back := now + int64(states.BackoffFor(int(fresh.ThrottleCount)).Seconds()); back > wake {
			wake = back
			v.log("  %s: backing off %ds after %d degraded handoff(s)", agent, back-now, fresh.ThrottleCount)
		}
	}
	if hid != "" {
		check(db.AdvanceHandoff(m.Q, hid, "released", ifTrue(ok, "done")+ifTrue(!ok, "degraded"), ""))
	}
	check(db.SetAgentState(m.Q, agent, states.Parked, wake, fmt.Sprintf("%s; wakes at %s", label, timefmt.Stamp(wake, timefmt.Clock))))
	var acctV, srcV any
	if have {
		acctV, srcV = acct, source
	}
	must(db.AddEvent(m.Q, agent, "park", db.KV{K: "kind", V: t.Kind}, db.KV{K: "wake_at", V: wake}, db.KV{K: "account", V: acctV}, db.KV{K: "account_source", V: srcV}))
	check(db.BumpCounter(m.Q, agent, "continuations", 1))
	v.log("  %s: PARKED until %s", agent, timefmt.Stamp(wake, timefmt.DateTime))
}

// ResumeHandoff finishes a handoff from the step it reached, so a restart picks up where it landed instead of leaving the agent in
// HANDOFF. Steps run requested -> recorded -> extracted -> released, each committed. Nothing is ended here: a daemon that was restarted
// owns no session process, and the sweep records the agent closed.
func (v *Mover) ResumeHandoff(row db.HandoffRow) (err error) {
	defer catch(&err)
	m := v.M
	agent, step, hid := row.Agent, row.Step, row.ID
	doneOK := true
	if step == "requested" {
		doneOK = v.recordHandoff(agent, hid)
		if doneOK {
			check(db.ResetCounter(m.Q, agent, "throttle_count"))
		}
	}
	if step == "requested" || step == "recorded" || step == "folded" {
		check(db.AdvanceHandoff(m.Q, hid, "extracted", "in_flight", ""))
	}
	acct, source, have := v.E.Reading(m.Q, agent)
	kind := row.TriggerKind.String
	shortRetry := !have || source == "assumed" || kind == "forced"
	now := m.now()
	var wake int64
	switch {
	case shortRetry:
		wake = now + states.AssumedAccountRecheckS
	case usageTrigger(kind) || kind == states.ByHandMark:
		w := v.E.Windows(acct)
		wake = v.E.WakeAfter(kind, w.ShortReset, w.LongReset, now)
	default:
		wake = now
	}
	check(db.AdvanceHandoff(m.Q, hid, "released", ifTrue(doneOK, "done")+ifTrue(!doneOK, "degraded"), ""))
	label := "resumed handoff"
	switch {
	case kind == states.ByHandMark:
		label = states.ByHandMark
	case shortRetry:
		label = states.MovingMark
	}
	check(db.SetAgentState(m.Q, agent, states.Parked, wake, fmt.Sprintf("%s; wakes at %s", label, timefmt.Stamp(wake, timefmt.Clock))))
	var acctV, srcV any
	if have {
		acctV, srcV = acct, source
	}
	must(db.AddEvent(m.Q, agent, "park", db.KV{K: "kind", V: kind}, db.KV{K: "wake_at", V: wake}, db.KV{K: "account", V: acctV}, db.KV{K: "account_source", V: srcV}))
	v.log("  %s: resumed handoff from '%s' -> PARKED", agent, step)
	return nil
}
