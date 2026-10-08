package supervisor

import (
	"fmt"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/timefmt"
)

// Background work behind a session's prompt, and a resolve nobody wrote (daemon/delivery.py).

const (
	// NudgeAfterS: an agent is asked to resolve the request at the head of its queue once it has been idle this long with nothing of its own
	// outstanding.
	NudgeAfterS = 600
	// ForgottenStuckS is the longest silence the daemon calls healthy, so the screen and the RUNNING label agree on what stuck is.
	ForgottenStuckS = states.StuckRunningS
	// NudgeRetryS: a nudge that could not be typed is not asked again for this long, because the idle probe that says no costs its whole timeout.
	NudgeRetryS = 60
	// StaleBgS: background work the agent has shown nothing beside this long is stale, under the hour a prompt cache lives.
	StaleBgS = 3000
	// StaleBgVetoS: once the supervisor has been told, this long passes before the work may be stopped.
	StaleBgVetoS = 600
)

// NudgeScheduleS is how many times a forgotten head is nudged, and how long it must have sat idle for each: ten minutes, thirty, then two
// hours. Past the last one nothing more is typed at the agent.
var NudgeScheduleS = []int64{NudgeAfterS, ForgottenStuckS, 2 * 3600}

// TrackBackgroundWork records whether an open session's own footer reports work still running behind its prompt, in whatever state it is
// waiting. Read every tick, never assumed from a clock. When the footer stops reporting it, the idle clock starts there (IdleSince).
func (e *Env) TrackBackgroundWork(q db.Q, agent string, row *db.AgentState) (err error) {
	defer Catch(&err)
	key := "bgwork:" + agent
	if row != nil && (row.ClosedAt.Valid && row.ClosedAt.Int64 != 0 || row.Terminal.String == "") {
		check(db.ClearCursor(q, key)) // the session is gone, and so is what ran behind its prompt
		check(db.ClearCursor(q, "bgcount:"+agent))
		return nil
	}
	if row == nil || row.State == states.Handoff || row.State == states.Stalled || !e.Terminal.Available() {
		return nil
	}
	running, known := e.Terminal.BackgroundWork(row.Terminal.String)
	if !known {
		return nil // unreadable is not "ended"
	}
	if running != "" {
		n := e.Idle.FooterCount(running)
		if n < 1 {
			n = 1
		}
		check(db.SetCursor(q, "bgcount:"+agent, int64(n)))
		e.Channel.RecordScreenSample(q, agent, row, running, e.Terminal.LastTail(row.Terminal.String))
	}
	have := must(db.Cursor(q, key)) != 0
	if running != "" && !have {
		check(db.SetCursor(q, key, 1))
	} else if running == "" && have {
		check(db.ClearCursor(q, key))
		check(db.ClearCursor(q, "bgcount:"+agent))
		must(db.AddEvent(q, agent, "background_work_ended"))
	}
	return nil
}

// IdleSince is when the agent last did anything, its background work last ended, or its session was last brought back.
func (e *Env) idleSince(q db.Q, agent string) (int64, bool) {
	var best int64
	found := false
	if t, ok := ok2(db.LastActivity(q, agent)); ok {
		best, found = t, true
	}
	for _, kind := range []string{"background_work_ended", "wake"} {
		if t, ok := ok2(db.LastEventTS(q, agent, kind)); ok && (!found || t > best) {
			best, found = t, true
		}
	}
	return best, found
}

// HasBackgroundWork: a closed session runs nothing, whatever the cursor still says from before it closed.
func (e *Env) hasBackgroundWork(q db.Q, agent string) bool {
	row := stateOf(q, agent)
	return must(db.Cursor(q, "bgwork:"+agent)) != 0 && !(row != nil && row.ClosedAt.Valid && row.ClosedAt.Int64 != 0)
}

// BgOwesNothing: no request queued for it, none it asked and still waits on, no task it is doing.
func (e *Env) bgOwesNothing(q db.Q, agent string) bool {
	if len(must(db.QueuedFor(q, agent))) > 0 || len(must(db.OpenAskIDs(q, agent))) > 0 {
		return false
	}
	tid, ok := ok2(db.CurrentTask(q, agent))
	return !(ok && must(db.ReadsDoing(q, tid)))
}

// BgPromptedAt is when the agent was told to finish or stop its background work, in this stretch.
func (e *Env) bgPromptedAt(q db.Q, agent string) int64 { return must(db.Cursor(q, "bg_prompt:"+agent)) }

// BgSince is when the daemon first saw this agent's session report background work in this stretch.
func (e *Env) bgSince(q db.Q, agent string) (int64, bool) {
	if !e.hasBackgroundWork(q, agent) {
		return 0, false
	}
	return ok2(db.CursorUpdated(q, "bgwork:"+agent))
}

// BgLastShown is when the agent last showed anything, not counting the turns that answered a background check: each check asks for an answer,
// and an agent that gives one every N minutes would otherwise never be silent long enough to be told to stop, or to be stale.
func (e *Env) bgLastShown(q db.Q, agent string) (int64, bool) {
	since, _ := e.bgSince(q, agent)
	checks := must(idsOf(q, "SELECT id FROM events WHERE agent = ? AND kind = 'background_checked' AND ts >= ? ORDER BY id", agent, since))
	if len(checks) == 0 {
		return ok2(db.LastActivity(q, agent))
	}
	args := []any{agent, checks[0]}
	for _, k := range db.ActivityKinds {
		args = append(args, k)
	}
	marks := "?"
	for i := 1; i < len(db.ActivityKinds); i++ {
		marks += ",?"
	}
	rs, err := q.Query("SELECT id, ts, kind FROM events WHERE agent = ? AND id > ? AND kind IN ("+marks+") ORDER BY id", args...)
	check(err)
	type ev struct {
		id, ts int64
		kind   string
	}
	var rows []ev
	for rs.Next() {
		var r ev
		check(rs.Scan(&r.id, &r.ts, &r.kind))
		rows = append(rows, r)
	}
	rs.Close()
	args = []any{agent, checks[0]}
	for _, k := range db.ActivityKinds {
		args = append(args, k)
	}
	var f any
	err = q.QueryRow("SELECT MAX(ts) FROM events WHERE agent = ? AND id < ? AND kind IN ("+marks+")", args...).Scan(&f)
	check(err)
	shown, have := int64(0), false
	if f != nil {
		shown, have = f.(int64), true
	}
	answering, i := false, 0
	for _, r := range rows {
		for i < len(checks) && checks[i] < r.id { // by event order, not by clock
			answering, i = true, i+1
		}
		if !answering {
			shown, have = r.ts, true
		} else if r.kind == "Stop" || r.kind == "StopFailure" {
			answering = false
		}
	}
	return shown, have
}

// BgSilentS is how long this agent has shown nothing while its session reports background work, ok false without any.
func (e *Env) bgSilentS(q db.Q, agent string, now int64) (int64, bool) {
	since, ok := e.bgSince(q, agent)
	if !ok {
		return 0, false
	}
	last, shown := e.bgLastShown(q, agent)
	if !shown || last == 0 {
		last = since
	}
	if d := now - last; d > 0 {
		return d, true
	}
	return 0, true
}

// BgStale: forgotten background work, shown nothing beside it for StaleBgS. Never while the agent is RUNNING: one foreground step can run that
// long with no hook event, and a turn stuck that long is reported elsewhere.
func (e *Env) bgStale(q db.Q, agent string, now int64) bool {
	if row := stateOf(q, agent); row != nil && row.State == states.Running {
		return false
	}
	silent, ok := e.bgSilentS(q, agent, now)
	return ok && silent >= e.Idle.ConfigSeconds("stale_background_s", StaleBgS)
}

// BgMayStop: an agent that owes nothing is told to finish or stop the work, and shown nothing for its grace window since. Any other: stale, and
// told to its supervisor at least StaleBgVetoS ago. Never an agent whose close timeout is NEVER, the veto.
func (e *Env) bgMayStop(q db.Q, agent string, now int64) bool {
	if e.Idle.CloseTimeoutS(agent) == e.Idle.Never() {
		return false
	}
	if e.bgOwesNothing(q, agent) {
		prompted := e.bgPromptedAt(q, agent)
		last, _ := ok2(db.LastActivity(q, agent))
		return prompted != 0 && e.hasBackgroundWork(q, agent) && now-max(prompted, last) >= e.Idle.BgGraceS(agent)
	}
	told := must(db.Cursor(q, "stale_bg_told:"+agent))
	return told != 0 && now-told >= StaleBgVetoS && e.bgStale(q, agent, now)
}

// BgHolds: does background work keep this agent from being closed or typed past?
func (e *Env) bgHolds(q db.Q, agent string, now int64) bool {
	return e.hasBackgroundWork(q, agent) && !e.bgMayStop(q, agent, now)
}

// waitingOnAsk: does the agent have an open ask it may be waiting on instead of head? Not the ones that are why a task ahead of head is
// blocked: head stands in for that task, so the agent is meant to be working it while they are open.
func (e *Env) waitingOnAsk(q db.Q, agent string, head *db.Request) bool {
	asks := map[int64]bool{}
	for _, id := range must(db.OpenAskIDs(q, agent)) {
		asks[id] = true
	}
	trueHead := must(db.TaskHead(q, agent, 0))
	if len(asks) > 0 && trueHead != nil && trueHead.ID != head.ID && e.headTaskBlocked(q, agent, trueHead) {
		for _, t := range must(db.TasksForRequest(q, trueHead.ID)) {
			for _, r := range must(db.TaskRequests(q, t.ID)) {
				delete(asks, r.ID)
			}
		}
	}
	return len(asks) > 0
}

// Forgotten is what the agent is holding when it looks like a resolve nobody wrote.
type Forgotten struct {
	Head    *db.Request
	IdleS   int64
	Behind  int
	hasBack bool
}

// ForgottenResolve is the agent's next task (EffectiveTaskHead) when that was told and is still open: IDLE or QUEUED with a live terminal,
// delivered and told and still open, and nothing the agent asked that is still unanswered. Idle time counts from the later of its last
// activity and the delivery. nil when it is not that.
func (e *Env) forgottenResolve(q db.Q, agent string, row *db.AgentState, now int64) *Forgotten {
	if row == nil || (row.State != states.Idle && row.State != states.Queued) || (row.ClosedAt.Valid && row.ClosedAt.Int64 != 0) || !e.openSession(agent, row) {
		return nil
	}
	if e.hasBackgroundWork(q, agent) && !e.bgStale(q, agent, now) {
		return nil // not idle: work it started is still running
	}
	head := e.effectiveTaskHead(q, agent)
	if head == nil || !head.DeliveredAt.Valid || must(db.Cursor(q, "notify:"+agent)) != head.ID {
		return nil
	}
	if e.waitingOnAsk(q, agent, head) || len(must(db.UnannouncedAnswers(q, agent))) > 0 {
		return nil
	}
	since, _ := e.idleSince(q, agent)
	idle := now - max(head.DeliveredAt.Int64, since)
	depth := must(db.QueueDepth(q, agent))
	return &Forgotten{Head: head, IdleS: idle, Behind: depth - 1}
}

// NudgeCount is how many times the current head has already been nudged: 0 for a head that is not the one the nudge cursor names.
func (e *Env) nudgeCount(q db.Q, agent string, headID int64) int64 {
	if must(db.Cursor(q, "nudge:"+agent)) == headID {
		return must(db.Cursor(q, "nudge_count:"+agent))
	}
	return 0
}

// NudgeExhausted: NudgeScheduleS is used up for this head, so nothing more will be typed at the agent about it.
func (e *Env) nudgeExhausted(q db.Q, agent string, headID int64) bool {
	return e.nudgeCount(q, agent, headID) >= int64(len(NudgeScheduleS))
}

// NudgeForgottenResolve types a reminder at an agent that has left its head request open, again at each rung of NudgeScheduleS for as long as
// that stays true, up to its last one. Never resolves for it: the verdict is the agent's to write. Gated like a delivery, and nothing here touches
// an attempt counter, so it cannot stall an agent.
func (e *Env) NudgeForgottenResolve(q db.Q, agent string, row *db.AgentState, now int64) (sent bool, err error) {
	defer Catch(&err)
	found := e.forgottenResolve(q, agent, row, now)
	if found == nil {
		return false, nil
	}
	head := found.Head
	if e.headTaskBlocked(q, agent, head) {
		return false, nil
	}
	count := e.nudgeCount(q, agent, head.ID)
	if count >= int64(len(NudgeScheduleS)) || found.IdleS < NudgeScheduleS[count] {
		return false, nil
	}
	if now-must(db.Cursor(q, "nudge_try:"+agent)) < NudgeRetryS {
		return false, nil
	}
	check(db.SetCursor(q, "nudge_try:"+agent, now))
	idle := timefmt.Ago(found.IdleS)
	after := ""
	if found.Behind > 0 {
		after = fmt.Sprintf(" with %d more waiting behind it", found.Behind)
	}
	var text string
	var task *db.Task
	if head.Kind.String == "task" {
		task = taskOf(q, head)
	}
	if task != nil {
		text = fmt.Sprintf("T%d (#%d) is still yours and you have been idle %s%s. Done: hand it on (`hl task handoff T%d ...`) or `hl task done T%d`. "+
			"Waiting on something: record it - `hl task edit T%d --blocked-by T<m>`, or a child or decision task.", task.ID, head.ID, idle, after, task.ID, task.ID, task.ID)
	} else {
		text = fmt.Sprintf("#%d was delivered to you and is still open, and you have been idle %s%s. If the work is done, %s; if it is not, say what you are waiting on.",
			head.ID, idle, after, answerVerb(q, head))
	}
	sent, err = e.TypeNotice(q, agent, text)
	if err != nil {
		return false, err
	}
	if sent {
		check(db.SetCursor(q, "nudge:"+agent, head.ID))
		check(db.SetCursor(q, "nudge_count:"+agent, count+1))
		must(db.AddEvent(q, agent, "nudged", db.KV{K: "request_id", V: head.ID}, db.KV{K: "idle_s", V: found.IdleS}, db.KV{K: "behind", V: found.Behind}))
		e.log("  %s: idle %s with #%d open and nothing outstanding - nudged to resolve it", agent, timefmt.Ago(found.IdleS), head.ID)
	}
	return sent, nil
}
