package tick

import (
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/timefmt"
)

// Five notices about work that has run long, all read from the db every tick.
//
// A task's own timeout (`ocp task new -t/--timeout`) only counts while it is doing; past it, its opener is told, and again every
// further timeout_s stretch, until the task answers with one of `hl task edit --timeout` (continue) or `hl task release` (back to queued) -
// or is simply done. An agent that shows RUNNING - working, or with background work behind its prompt - while nothing is asked of it tells
// its supervisor once, so the manager can ask what it is doing. Background work with the agent silent for STALE_BG_S tells its supervisor it
// may be stopped after a veto window. An agent IDLE with its current task's own wait never recorded tells the founder once, the Stop-hook
// gate's own backstop. An agent nudged to resolve a forgotten head as many times as the schedule allows, still open, tells its
// supervisor once - nothing more is sent to the agent about it past that. The first three act on nothing.
//
// A notice to an agent is a line sent to its open session, so one that cannot be sent now is tried again; a notice to the human has no
// session to type at and is an event.
const (
	UnaskedRunningS   = 900
	TitleShown        = 60
	StopWaitBackstopS = 900
	// NoticeRetryS: a notice that could not be sent (mid-turn, awaiting a permission, no session) is not tried again for this long, per agent.
	NoticeRetryS = 60
)

// Forgotten is delivery.forgotten_resolve with the questions timebox asks about its head: an idle agent whose current head request nobody
// resolved. Task is the head's task when it is one.
type Forgotten struct {
	HeadID    int64
	Task      *db.Task
	IdleS     int64
	Behind    int
	Blocked   bool // delivery.head_task_blocked
	Exhausted bool // delivery.nudge_exhausted: the nudge schedule is used up
}

// BgJob is one running background job as bgjobs reports it.
type BgJob struct{ Line string }

// TimeboxPorts are the foreign calls the notices make: the board's actions, delivery's background bookkeeping, and the idle-close settings.
type TimeboxPorts interface {
	// TaskDoing is actions.task_state(row) == "doing"; TaskBegun is actions._begun.
	TaskDoing(q db.Q, t *db.Task) bool
	TaskBegun(q db.Q, tid int64) bool
	// SupervisorOf is actions.supervisor_of(routing(), agent), "" when it has none.
	SupervisorOf(agent string) string
	// ChildWait is actions.child_wait; StopWaitReason actions.stop_wait_reason ("" for none).
	ChildWait(q db.Q, agent string) bool
	StopWaitReason(q db.Q, agent string) string
	// SendNotice is delivery.type_notice, sent through the session: true when the line was sent.
	SendNotice(q db.Q, agent, text string) bool
	BgStale(q db.Q, agent string, now int64) bool
	BgOwesNothing(q db.Q, agent string) bool
	BgSilentS(q db.Q, agent string, now int64) (int64, bool)
	BgSince(q db.Q, agent string) (int64, bool)
	BgPromptedAt(q db.Q, agent string) int64
	StaleBgVetoS() int64
	ForgottenResolve(q db.Q, agent string, row *db.AgentState, now int64) *Forgotten
	NudgeScheduleLen() int
	// NeverCloses is idle_close.close_timeout_s(agent) == NEVER.
	NeverCloses(agent string) bool
	BgNoticeS(agent string) int64
	BgGraceS(agent string) int64
	BgCheckS(agent string) int64
	// BgJobs is bgjobs.running(con, agent, count) already formatted one line per job (bgjobs.line).
	BgJobs(q db.Q, agent string, count int, now int64) []string
}

// Timebox is the five notices.
type Timebox struct {
	Ports TimeboxPorts
	// Cutover: the Python daemon no longer ticks, so the task clock covers every owner; before it, only the owners this daemon drives (the
	// Python clock skips these, and a clock run by both tells an opener twice).
	Cutover bool
}

func (b *Timebox) tell(t *Tick, who, text string, now int64) bool {
	if who == db.Human {
		return true
	}
	key := "notice_try:" + who
	if now-must(db.Cursor(t.Q, key)) < NoticeRetryS {
		return false
	}
	check(db.SetCursor(t.Q, key, now))
	return b.Ports.SendNotice(t.Q, who, text)
}

func (b *Timebox) stateOf(t *Tick, agent string) string {
	row := must(db.GetAgentState(t.Q, agent))
	if row == nil {
		return "unknown"
	}
	bg := ""
	if t.Delivery.HasBackgroundWork(t.Q, agent) {
		bg = " with background work"
	}
	detail := ""
	if row.Detail.String != "" {
		detail = " (" + row.Detail.String + ")"
	}
	return row.State + bg + detail
}

func (b *Timebox) boss(agent string) string {
	if s := b.Ports.SupervisorOf(agent); s != "" {
		return s
	}
	return db.Human
}

// SweepTaskTimeouts is a task's timeout clock, read each tick: it runs only while the task is doing; past it the opener is told, and again
// every stretch until it is done, extended or released. A task with no timeout_s takes the default where it becomes doing.
func (b *Timebox) SweepTaskTimeouts(t *Tick, now int64) {
	driven := map[string]bool{}
	for _, a := range t.Host.Agents() {
		driven[a] = true
	}
	for _, row := range must(db.TasksToClock(t.Q)) {
		if !b.Cutover && !driven[row.Owner] {
			continue // a terminal agent's task: the Python daemon's clock
		}
		tid := row.ID
		if row.Reported() {
			b.remindReported(t, row, now)
			continue
		}
		if !b.Ports.TaskDoing(t.Q, row) {
			if row.TimeoutAt.Valid {
				check(db.PauseTaskTimeout(t.Q, tid))
			}
			continue
		}
		if !row.TimeoutAt.Valid {
			check(db.AdvanceTaskTimeout(t.Q, tid, now, db.DefaultTaskTimeoutS))
			continue
		}
		if row.TimeoutAt.Int64 > now {
			continue
		}
		box := row.TimeoutS.Int64
		title := shortTitle(row.Title)
		// A task begun on a founder's message has no queue to go back to: release refuses it.
		stop := ""
		if !b.Ports.TaskBegun(t.Q, tid) {
			stop = fmt.Sprintf(" Stop it: `hl task release T%d` sends it back to queued.", tid)
		}
		text := fmt.Sprintf("T%d (%s), held by %s, has been doing past its %s timeout. Keep it going: `hl task edit T%d --timeout <duration>`.%s",
			tid, title, row.Owner, timefmt.Ago(box), tid, stop)
		if b.tell(t, row.OpenedBy, text, now) {
			check(db.AdvanceTaskTimeout(t.Q, tid, now, db.DefaultTaskTimeoutS))
			// the deadline missed and the one that replaced it: a screen chains notices by them
			next := must(db.GetTask(t.Q, tid))
			var nextAt any
			if next != nil && next.TimeoutAt.Valid {
				nextAt = next.TimeoutAt.Int64
			}
			must(db.AddEvent(t.Q, row.OpenedBy, "task_timeout_exceeded", db.KV{K: "task_id", V: tid}, db.KV{K: "timeout_s", V: box},
				db.KV{K: "text", V: text}, db.KV{K: "deadline", V: row.TimeoutAt.Int64}, db.KV{K: "next", V: nextAt}))
			t.logf("  T%d: doing, past its %s timeout - %s told", tid, timefmt.Ago(box), row.OpenedBy)
		}
	}
}

// shortTitle is a task's title on one line, cut to TitleShown.
func shortTitle(title string) string {
	title = strings.Join(strings.Fields(title), " ")
	if r := []rune(title); len(r) > TitleShown {
		title = strings.TrimRight(string(r[:TitleShown]), " \t\n") + "…"
	}
	return title
}

// remindReported is the clock of a task whose done waits on acceptance: it runs on from where the doing left it, and past it whoever the task
// answers to is reminded, again every stretch until they accept or reject it.
func (b *Timebox) remindReported(t *Tick, row *db.Task, now int64) {
	if !row.TimeoutAt.Valid || row.TimeoutAt.Int64 > now {
		return
	}
	to := row.AnswersTo()
	text := fmt.Sprintf("T%d (%s) was reported done by %s and waits on you: accept it (`hl task accept T%d`) or send it back (`hl task reject T%d \"<note>\"`).",
		row.ID, shortTitle(row.Title), row.Owner, row.ID, row.ID)
	if b.tell(t, to, text, now) {
		check(db.AdvanceTaskTimeout(t.Q, row.ID, now, db.DefaultTaskTimeoutS))
		next := must(db.GetTask(t.Q, row.ID))
		must(db.AddEvent(t.Q, to, "task_timeout_exceeded", db.KV{K: "task_id", V: row.ID}, db.KV{K: "timeout_s", V: row.TimeoutS.Int64},
			db.KV{K: "text", V: text}, db.KV{K: "deadline", V: row.TimeoutAt.Int64}, db.KV{K: "next", V: next.TimeoutAt.Int64}))
		t.logf("  T%d: reported, past its timeout - %s reminded", row.ID, to)
	}
}

func (b *Timebox) unaskedRunning(t *Tick, agent string, row *db.AgentState) bool {
	if row == nil || closedAt(row) || !t.Host.SessionAlive(agent) || len(must(db.QueuedFor(t.Q, agent))) > 0 {
		return false
	}
	// Background work does not exempt a RUNNING agent: the sweeps that settle it leave an agent mid-turn alone, so this is its
	// one report.
	if row.State != states.Running {
		return false
	}
	return !b.Ports.ChildWait(t.Q, agent) // waiting on a child of its own is a reason
}

// SweepUnaskedRunning tells an agent's supervisor once that it has shown RUNNING for long with nothing asked of it.
func (b *Timebox) SweepUnaskedRunning(t *Tick, now int64) {
	limit := t.Config.Seconds("unasked_running_s", UnaskedRunningS)
	for _, agent := range t.Host.Agents() {
		sinceKey, toldKey := "unasked_since:"+agent, "unasked_told:"+agent
		if !b.unaskedRunning(t, agent, must(db.GetAgentState(t.Q, agent))) {
			check(db.ClearCursor(t.Q, sinceKey))
			check(db.ClearCursor(t.Q, toldKey))
			continue
		}
		since := must(db.Cursor(t.Q, sinceKey))
		if since == 0 {
			check(db.SetCursor(t.Q, sinceKey, now))
			continue
		}
		if now-since < limit || must(db.Cursor(t.Q, toldKey)) != 0 {
			continue
		}
		boss := b.boss(agent)
		text := fmt.Sprintf("%s has shown RUNNING (%s) for %s with no request open. Ask it what it is doing: `hl say %s \"...\"`.",
			agent, b.stateOf(t, agent), timefmt.Ago(now-since), agent)
		if b.tell(t, boss, text, now) {
			check(db.SetCursor(t.Q, toldKey, 1))
			must(db.AddEvent(t.Q, agent, "unasked_running", db.KV{K: "told", V: boss}, db.KV{K: "for_s", V: now - since}))
			t.logf("  %s: RUNNING %s with no request open - %s told", agent, timefmt.Ago(now-since), boss)
		}
	}
}

// SweepStaleBackground tells the supervisor once that an agent's background work is stale, and that its session may be closed - which
// stops the work - after the veto window. The mark is cleared when the work is no longer stale, so a later stretch is told again.
func (b *Timebox) SweepStaleBackground(t *Tick, now int64) {
	for _, agent := range t.Host.Agents() {
		key := "stale_bg_told:" + agent
		if !b.Ports.BgStale(t.Q, agent, now) || b.Ports.BgOwesNothing(t.Q, agent) || b.Ports.NeverCloses(agent) {
			check(db.ClearCursor(t.Q, key))
			continue
		}
		if must(db.Cursor(t.Q, key)) != 0 {
			continue
		}
		boss := b.boss(agent)
		silent, _ := b.Ports.BgSilentS(t.Q, agent, now)
		since, have := b.Ports.BgSince(t.Q, agent)
		if !have {
			since = now
		}
		text := fmt.Sprintf("%s has had background work for %s and shown nothing for %s. In %s its session may be closed, which stops that work, "+
			"unless it shows activity or `hl idle-timeout never --agent %s` is set.",
			agent, timefmt.Ago(now-since), timefmt.Ago(silent), timefmt.Ago(b.Ports.StaleBgVetoS()), agent)
		if b.tell(t, boss, text, now) {
			check(db.SetCursor(t.Q, key, now))
			must(db.AddEvent(t.Q, agent, "stale_background", db.KV{K: "told", V: boss}, db.KV{K: "silent_s", V: silent}))
			t.logf("  %s: background work stale (%s silent) - %s told", agent, timefmt.Ago(silent), boss)
		}
	}
}

// SweepForgottenBackground: background work nobody waits on settles itself, with nobody asked. When the agent's idle window is over it is
// told once to finish or stop the work; bg_grace_s later, with nothing shown, the idle close ends the session and its work. An agent set
// to never close is left alone. Both marks clear with the stretch: when the work ends, or a request, an ask or a task makes it owed.
func (b *Timebox) SweepForgottenBackground(t *Tick, now int64) {
	for _, agent := range t.Host.Agents() {
		key, noted := "bg_prompt:"+agent, "bg_stop_noted:"+agent
		if !t.Delivery.HasBackgroundWork(t.Q, agent) || !b.Ports.BgOwesNothing(t.Q, agent) || b.Ports.NeverCloses(agent) {
			check(db.ClearCursor(t.Q, key))
			check(db.ClearCursor(t.Q, noted))
			continue
		}
		silent, haveSilent := b.Ports.BgSilentS(t.Q, agent, now)
		if b.Ports.BgPromptedAt(t.Q, agent) == 0 {
			if !haveSilent || silent < b.Ports.BgNoticeS(agent) {
				continue
			}
			text := fmt.Sprintf("You have shown nothing for %s, with no request open, and background work is still running. Finish it or stop it now, "+
				"and say in one line what it was. If nothing more is shown in %s, it is stopped and this session closed.",
				timefmt.Ago(silent), timefmt.Ago(b.Ports.BgGraceS(agent)))
			if b.tell(t, agent, text, now) {
				check(db.SetCursor(t.Q, key, now))
				must(db.AddEvent(t.Q, agent, "background_prompted", db.KV{K: "silent_s", V: silent}))
				t.logf("  %s: background work with nothing owed, silent %s - told to finish or stop it", agent, timefmt.Ago(silent))
			}
		}
	}
}

// SweepBackgroundCheck: every bg_check_s while an agent has background work, at a turn's end, it is told what is running and asked
// to stop what it no longer needs - a shell nobody stops holds off the context rollover. Not the idle close: busy or idle, owing or not, a
// "never close" agent too. It waits out a notice already in flight.
func (b *Timebox) SweepBackgroundCheck(t *Tick, now int64) {
	for _, agent := range t.Host.Agents() {
		key := "bg_check:" + agent
		row := must(db.GetAgentState(t.Q, agent))
		if row == nil || !t.Delivery.HasBackgroundWork(t.Q, agent) {
			check(db.ClearCursor(t.Q, key))
			continue
		}
		if row.State != states.Idle || b.Ports.BgPromptedAt(t.Q, agent) != 0 {
			continue
		}
		every := b.Ports.BgCheckS(agent)
		since, have := b.Ports.BgSince(t.Q, agent)
		if !have {
			since = now
		}
		last := must(db.Cursor(t.Q, key))
		if last < since {
			last = since
		}
		if now-last < every {
			continue
		}
		count := int(must(db.Cursor(t.Q, "bgcount:"+agent)))
		jobs := b.Ports.BgJobs(t.Q, agent, count, now)
		named := count
		if named == 0 {
			named = len(jobs)
		}
		unnamed := named - len(jobs)
		if unnamed < 0 {
			unnamed = 0
		}
		var lines []string
		for i, j := range jobs {
			lines = append(lines, fmt.Sprintf("%d. %s", i+1, j))
		}
		listing := strings.Join(lines, "; ")
		if unnamed > 0 {
			if listing != "" {
				listing += "; "
			}
			listing += fmt.Sprintf("%d that ocp cannot name", unnamed)
		}
		if listing == "" {
			listing = "something ocp cannot name"
		}
		text := fmt.Sprintf("Background work has been running for %s: %s. Stop what you no longer need, and for each one you keep say in a line why. "+
			"If it watches for another task, stop it and set `--blocked-by T<n>` on your task instead. You are asked again in %s while any runs.",
			timefmt.Ago(now-since), listing, timefmt.Ago(every))
		// The event goes in before the send: its id is what separates the turn that ended before the check from the one that answers it.
		checked := must(db.AddEvent(t.Q, agent, "background_checked", db.KV{K: "jobs", V: len(jobs)}, db.KV{K: "unnamed", V: unnamed}))
		told := false
		func() {
			defer func() {
				if !told { // nothing sent, or the send raised: no check was asked
					_, _ = t.Q.Exec("DELETE FROM events WHERE id = ?", checked)
				}
			}()
			told = b.tell(t, agent, text, now)
		}()
		if told {
			check(db.SetCursor(t.Q, key, now))
			t.logf("  %s: background work for %s - asked which to stop", agent, timefmt.Ago(now-since))
		}
	}
}

// SweepStopWaitBackstop is the gate's own backstop: the clock starts the moment the task-level wait goes missing and only clears once it is
// recorded or the task moves on - even to another with no recorded wait in between; a RUNNING turn in between does not buy a fresh
// 15 minutes. It only tells while IDLE now, once, past the limit.
func (b *Timebox) SweepStopWaitBackstop(t *Tick, now int64) {
	limit := t.Config.Seconds("stop_wait_backstop_s", StopWaitBackstopS)
	for _, agent := range t.Host.Agents() {
		sinceKey, toldKey, taskKey := "stop_wait_since:"+agent, "stop_wait_told:"+agent, "stop_wait_task:"+agent
		row := must(db.GetAgentState(t.Q, agent))
		why := ""
		if row != nil && !closedAt(row) {
			why = b.Ports.StopWaitReason(t.Q, agent)
		}
		if why == "" {
			check(db.ClearCursor(t.Q, sinceKey))
			check(db.ClearCursor(t.Q, toldKey))
			check(db.ClearCursor(t.Q, taskKey))
			continue
		}
		tid, _ := must2(db.CurrentTask(t.Q, agent))
		if must(db.Cursor(t.Q, taskKey)) != tid { // the clock is the task's, not the agent's
			check(db.SetCursor(t.Q, taskKey, tid))
			check(db.ClearCursor(t.Q, sinceKey))
			check(db.ClearCursor(t.Q, toldKey))
		}
		since := must(db.Cursor(t.Q, sinceKey))
		if since == 0 {
			check(db.SetCursor(t.Q, sinceKey, now))
			continue
		}
		if row.State != states.Idle || now-since < limit || must(db.Cursor(t.Q, toldKey)) != 0 {
			continue
		}
		check(db.SetCursor(t.Q, toldKey, 1))
		must(db.AddEvent(t.Q, agent, "stop_wait_stuck", db.KV{K: "why", V: why}, db.KV{K: "for_s", V: now - since}))
		t.logf("  %s: %s, idle %s with nothing recorded - founder told", agent, why, timefmt.Ago(now-since))
	}
}

// SweepForgottenResolveCapped: the nudge about a forgotten head stops once its schedule is used up rather than
// nudging the agent forever - an agent that turns out to be genuinely stuck, not forgetful, still needs someone to look, so its
// supervisor is told once instead. Cleared as soon as it no longer applies, so a later head that is also forgotten and also runs out the
// schedule is told again.
func (b *Timebox) SweepForgottenResolveCapped(t *Tick, now int64) {
	for _, agent := range t.Host.Agents() {
		toldKey := "forgotten_capped_told:" + agent
		found := b.Ports.ForgottenResolve(t.Q, agent, must(db.GetAgentState(t.Q, agent)), now)
		if found == nil || found.Blocked || !found.Exhausted {
			check(db.ClearCursor(t.Q, toldKey))
			continue
		}
		if must(db.Cursor(t.Q, toldKey)) == found.HeadID {
			continue
		}
		boss := b.boss(agent)
		on := fmt.Sprintf("#%d", found.HeadID)
		if found.Task != nil {
			on = fmt.Sprintf("T%d (#%d)", found.Task.ID, found.HeadID)
		}
		after := fmt.Sprintf("after %d reminders", b.Ports.NudgeScheduleLen())
		more := ""
		if found.Behind > 0 {
			more = fmt.Sprintf(" with %d more waiting behind it", found.Behind)
		}
		var look string
		if found.Task != nil {
			// A task begun on a founder's message has no queue to go back to: release refuses it.
			back := ""
			if !b.Ports.TaskBegun(t.Q, found.Task.ID) {
				back = fmt.Sprintf("; take it back with `hl task release T%d`", found.Task.ID)
			}
			look = fmt.Sprintf("Look at it: `hl read %d`%s, or ask %s by message (`hl say %s \"...\"`).", found.HeadID, back, agent, agent)
		} else {
			look = fmt.Sprintf("Look at it: `hl read %d`, or message %s (`hl say %s \"...\"`).", found.HeadID, agent, agent)
		}
		text := fmt.Sprintf("%s has been idle %s on %s %s%s. %s", agent, timefmt.Ago(found.IdleS), on, after, more, look)
		if b.tell(t, boss, text, now) {
			check(db.SetCursor(t.Q, toldKey, found.HeadID))
			must(db.AddEvent(t.Q, agent, "forgotten_resolve_capped", db.KV{K: "request_id", V: found.HeadID}, db.KV{K: "idle_s", V: found.IdleS}, db.KV{K: "told", V: boss}))
			t.logf("  %s: idle %s on #%d, nudge cap reached - %s told", agent, timefmt.Ago(found.IdleS), found.HeadID, boss)
		}
	}
}

// Sweep is timebox.sweep: the notices in order. Each one's failure stops the rest, as an exception did.
func (b *Timebox) Sweep(t *Tick, now int64) error {
	return guard(func() {
		b.SweepTaskTimeouts(t, now)
		b.SweepUnaskedRunning(t, now)
		b.SweepStaleBackground(t, now)
		b.SweepForgottenBackground(t, now)
		b.SweepBackgroundCheck(t, now)
		b.SweepStopWaitBackstop(t, now)
		b.SweepForgottenResolveCapped(t, now)
	})
}
