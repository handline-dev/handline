package tick

import (
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
)

// tbPorts scripts the timebox's foreign calls and keeps the notices it sent.
type tbPorts struct {
	TimeboxPorts
	doing      map[int64]bool
	begun      bool
	boss       string
	childWait  bool
	stopWait   string
	sendOK     bool
	sent       []string // "who: text"
	bgStale    bool
	bgOwes     bool
	never      bool
	silent     int64
	haveSilent bool
	since      int64
	haveSince  bool
	prompted   int64
	noticeS    int64
	graceS     int64
	checkS     int64
	jobs       []string
	forgotten  *Forgotten
}

func (p *tbPorts) TaskDoing(_ db.Q, t *db.Task) bool           { return p.doing[t.ID] }
func (p *tbPorts) TaskBegun(db.Q, int64) bool                  { return p.begun }
func (p *tbPorts) SupervisorOf(string) string                  { return p.boss }
func (p *tbPorts) ChildWait(db.Q, string) bool                 { return p.childWait }
func (p *tbPorts) StopWaitReason(db.Q, string) string          { return p.stopWait }
func (p *tbPorts) BgStale(db.Q, string, int64) bool            { return p.bgStale }
func (p *tbPorts) BgOwesNothing(db.Q, string) bool             { return p.bgOwes }
func (p *tbPorts) NeverCloses(string) bool                     { return p.never }
func (p *tbPorts) BgSilentS(db.Q, string, int64) (int64, bool) { return p.silent, p.haveSilent }
func (p *tbPorts) BgSince(db.Q, string) (int64, bool)          { return p.since, p.haveSince }
func (p *tbPorts) BgPromptedAt(db.Q, string) int64             { return p.prompted }
func (p *tbPorts) StaleBgVetoS() int64                         { return 600 }
func (p *tbPorts) BgNoticeS(string) int64                      { return p.noticeS }
func (p *tbPorts) BgGraceS(string) int64                       { return p.graceS }
func (p *tbPorts) BgCheckS(string) int64                       { return p.checkS }
func (p *tbPorts) BgJobs(db.Q, string, int, int64) []string    { return p.jobs }
func (p *tbPorts) NudgeScheduleLen() int                       { return 3 }
func (p *tbPorts) ForgottenResolve(db.Q, string, *db.AgentState, int64) *Forgotten {
	return p.forgotten
}
func (p *tbPorts) SendNotice(_ db.Q, who, text string) bool {
	p.sent = append(p.sent, who+": "+text)
	return p.sendOK
}

func timebox(t *testing.T) (*Tick, *world, *Timebox, *tbPorts) {
	tk, w := newWorld(t)
	p := &tbPorts{doing: map[int64]bool{}, sendOK: true, begun: true}
	return tk, w, &Timebox{Ports: p}, p
}

func newTask(t *testing.T, tk *Tick, owner, opener string, timeoutS int64) int64 {
	t.Helper()
	tid, err := db.NewTask(tk.Q, "the work  on the thing", owner, opener, 0, db.DefaultPriority, "report", timeoutS, "")
	if err != nil {
		t.Fatal(err)
	}
	return tid
}

func TestATaskThatIsDoingStartsItsClockThenTellsItsOpenerPastTheDeadlineAndRearms(t *testing.T) {
	tk, w, b, p := timebox(t)
	tid := newTask(t, tk, "a", "boss", 100)
	p.doing[tid] = true
	b.SweepTaskTimeouts(tk, 1000)
	task, _ := db.GetTask(tk.Q, tid)
	if !task.TimeoutAt.Valid || task.TimeoutAt.Int64 != 1100 || len(p.sent) != 0 {
		t.Fatalf("the clock starts where it becomes doing: %+v %v", task, p.sent)
	}
	b.SweepTaskTimeouts(tk, 1099)
	if len(p.sent) != 0 {
		t.Fatalf("told before the deadline: %v", p.sent)
	}
	b.SweepTaskTimeouts(tk, 1200)
	if len(p.sent) != 1 || !strings.HasPrefix(p.sent[0], "boss: T") || !strings.Contains(p.sent[0], "the work on the thing") ||
		!strings.Contains(p.sent[0], "held by a") || !strings.Contains(p.sent[0], "hl task edit T") {
		t.Fatalf("%v", p.sent)
	}
	if strings.Contains(p.sent[0], "Stop it") {
		t.Fatalf("a task begun on a founder's message has no queue to go back to: %v", p.sent)
	}
	task, _ = db.GetTask(tk.Q, tid)
	if task.TimeoutAt.Int64 != 1300 {
		t.Fatalf("the clock was not re-armed one stretch on: %+v", task)
	}
	if n := countEvents(t, tk, "boss", "task_timeout_exceeded"); n != 1 || !w.logged("past its") {
		t.Fatalf("events %d, log %v", n, w.logs)
	}
}

func TestATerminalAgentsTaskIsClockedOnlyAtTheCutover(t *testing.T) {
	tk, _, b, p := timebox(t)
	mine := newTask(t, tk, "a", "boss", 100) // "a" is driven here
	terminal := newTask(t, tk, "term", "boss", 100)
	p.doing[mine], p.doing[terminal] = true, true
	b.SweepTaskTimeouts(tk, 1000)
	b.SweepTaskTimeouts(tk, 1200)
	if task, _ := db.GetTask(tk.Q, terminal); task.TimeoutAt.Valid || len(p.sent) != 1 || !strings.Contains(p.sent[0], "held by a") {
		t.Fatalf("the Python daemon's clock was run too: %+v %v", task, p.sent)
	}
	b.Cutover = true
	b.SweepTaskTimeouts(tk, 1300)
	if task, _ := db.GetTask(tk.Q, terminal); !task.TimeoutAt.Valid || task.TimeoutAt.Int64 != 1400 {
		t.Fatalf("after the cutover every owner's task is clocked: %+v", task)
	}
}

func countEvents(t *testing.T, tk *Tick, agent, kind string) int {
	t.Helper()
	var n int
	if err := tk.Q.QueryRow("SELECT COUNT(*) FROM events WHERE agent = ? AND kind = ?", agent, kind).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

func TestATaskThatStopsBeingDoneHasItsClockPausedAndANoticeThatCannotBeSentIsRetried(t *testing.T) {
	tk, _, b, p := timebox(t)
	tid := newTask(t, tk, "a", "boss", 100)
	p.doing[tid] = true
	b.SweepTaskTimeouts(tk, 1000)
	p.doing[tid] = false
	b.SweepTaskTimeouts(tk, 1010)
	if task, _ := db.GetTask(tk.Q, tid); task.TimeoutAt.Valid {
		t.Fatalf("a task that is not doing keeps a deadline: %+v", task)
	}
	// Past the deadline with the opener's session busy: not marked told, so the next try (after the retry wait) sends it.
	p.doing[tid], p.sendOK = true, false
	b.SweepTaskTimeouts(tk, 2000)
	b.SweepTaskTimeouts(tk, 2200)
	if len(p.sent) != 1 {
		t.Fatalf("%v", p.sent)
	}
	p.sendOK = true
	b.SweepTaskTimeouts(tk, 2200+NoticeRetryS+1)
	if len(p.sent) != 2 || countEvents(t, tk, "boss", "task_timeout_exceeded") != 1 {
		t.Fatalf("%v", p.sent)
	}
}

func TestAWaitingTaskStopsItsClockAndResumesWithTheTimeItHadLeft(t *testing.T) {
	tk, _, b, p := timebox(t)
	tid := newTask(t, tk, "a", "boss", 100)
	blocker := newTask(t, tk, "c", "boss", 0)
	p.doing[tid] = true
	b.SweepTaskTimeouts(tk, 1000)
	if err := db.AddBlock(tk.Q, tid, blocker); err != nil {
		t.Fatal(err)
	}
	b.SweepTaskTimeouts(tk, 1040)
	b.SweepTaskTimeouts(tk, 1500) // past the first deadline, 1100
	if task, _ := db.GetTask(tk.Q, tid); task.TimeoutAt.Valid || len(p.sent) != 0 {
		t.Fatalf("a blocked task's clock ran: %+v %v", task, p.sent)
	}
	if _, err := db.RemoveBlock(tk.Q, tid, blocker); err != nil {
		t.Fatal(err)
	}
	b.SweepTaskTimeouts(tk, 1500)
	if task, _ := db.GetTask(tk.Q, tid); task.TimeoutAt.Int64 != 1100+460 {
		t.Fatalf("the deadline did not move by the 460s blocked: %+v", task)
	}
	b.SweepTaskTimeouts(tk, 1559)
	if len(p.sent) != 0 {
		t.Fatalf("told before the moved deadline: %v", p.sent)
	}
	b.SweepTaskTimeouts(tk, 1560)
	if len(p.sent) != 1 || !strings.HasPrefix(p.sent[0], "boss: T") {
		t.Fatalf("the opener is told at the moved deadline: %v", p.sent)
	}

	// an open child of its own stops it the same way, and a full stretch is re-armed after the notice
	child, err := db.NewTask(tk.Q, "part", "c", "a", tid, db.DefaultPriority, "report", 0, "")
	if err != nil {
		t.Fatal(err)
	}
	b.SweepTaskTimeouts(tk, 1600) // re-armed to 1660 at 1560: 60 left
	b.SweepTaskTimeouts(tk, 9000)
	if _, err := tk.Q.Exec("UPDATE tasks SET done_evidence = 'x' WHERE id = ?", child); err != nil {
		t.Fatal(err)
	}
	b.SweepTaskTimeouts(tk, 9000)
	if task, _ := db.GetTask(tk.Q, tid); task.TimeoutAt.Int64 != 9060 || len(p.sent) != 1 {
		t.Fatalf("a task waiting on its child resumes with what it had left: %+v %v", task, p.sent)
	}
}

func TestATaskOpenedByTheFounderIsToldAsAnEventAndNothingIsSent(t *testing.T) {
	tk, _, b, p := timebox(t)
	tid := newTask(t, tk, "a", db.Human, 100)
	p.doing[tid] = true
	b.SweepTaskTimeouts(tk, 1000)
	b.SweepTaskTimeouts(tk, 1200)
	if len(p.sent) != 0 || countEvents(t, tk, db.Human, "task_timeout_exceeded") != 1 {
		t.Fatalf("%v", p.sent)
	}
}

func TestAnAgentShowingRunningWithNothingAskedTellsItsSupervisorOnceAfterTheLimit(t *testing.T) {
	tk, w, b, p := timebox(t)
	p.boss = "lead"
	setState(t, tk, "a", states.Running, 0)
	b.SweepUnaskedRunning(tk, 1000) // the clock starts
	b.SweepUnaskedRunning(tk, 1000+UnaskedRunningS-1)
	if len(p.sent) != 0 {
		t.Fatalf("%v", p.sent)
	}
	b.SweepUnaskedRunning(tk, 1000+UnaskedRunningS)
	b.SweepUnaskedRunning(tk, 1000+UnaskedRunningS+500)
	if len(p.sent) != 1 || !strings.HasPrefix(p.sent[0], "lead: a has shown RUNNING") || !w.logged("lead told") {
		t.Fatalf("told once, to its supervisor: %v %v", p.sent, w.logs)
	}
	// It stops being unasked (waiting on a child): the marks clear, and a later stretch is told again.
	p.childWait = true
	b.SweepUnaskedRunning(tk, 5000)
	p.childWait = false
	b.SweepUnaskedRunning(tk, 5010+NoticeRetryS)
	b.SweepUnaskedRunning(tk, 5010+NoticeRetryS+UnaskedRunningS)
	if len(p.sent) != 2 {
		t.Fatalf("%v", p.sent)
	}
}

func TestStaleBackgroundWorkTellsTheSupervisorOnceAndClearsWhenItIsNoLongerStale(t *testing.T) {
	tk, _, b, p := timebox(t)
	p.boss, p.bgStale, p.silent, p.since, p.haveSince = "lead", true, 700, 100, true
	b.SweepStaleBackground(tk, 1000)
	b.SweepStaleBackground(tk, 1100)
	if len(p.sent) != 1 || !strings.Contains(p.sent[0], "background work for") || countEvents(t, tk, "a", "stale_background") != 1 {
		t.Fatalf("%v", p.sent)
	}
	p.bgStale = false
	b.SweepStaleBackground(tk, 1200)
	p.bgStale = true
	b.SweepStaleBackground(tk, 1300+NoticeRetryS)
	if len(p.sent) != 2 {
		t.Fatalf("a later stretch is told again: %v", p.sent)
	}
	// An agent set to never close is not told.
	tk, _, b, p = timebox(t)
	p.bgStale, p.never = true, true
	b.SweepStaleBackground(tk, 1000)
	if len(p.sent) != 0 {
		t.Fatalf("%v", p.sent)
	}
}

func TestForgottenBackgroundWorkIsToldToFinishOnceItsIdleWindowIsOver(t *testing.T) {
	tk, w, b, p := timebox(t)
	w.bgWork = true
	p.bgOwes, p.noticeS, p.graceS, p.haveSilent, p.silent = true, 600, 300, true, 599
	b.SweepForgottenBackground(tk, 1000)
	if len(p.sent) != 0 {
		t.Fatalf("%v", p.sent)
	}
	p.silent = 700
	b.SweepForgottenBackground(tk, 1100)
	if len(p.sent) != 1 || !strings.HasPrefix(p.sent[0], "a: You have shown nothing") || countEvents(t, tk, "a", "background_prompted") != 1 {
		t.Fatalf("%v", p.sent)
	}
	// Work that is owed (a request open) is not forgotten work.
	tk, w, b, p = timebox(t)
	w.bgWork, p.bgOwes, p.haveSilent, p.silent, p.noticeS = true, false, true, 9999, 600
	b.SweepForgottenBackground(tk, 1000)
	if len(p.sent) != 0 {
		t.Fatalf("%v", p.sent)
	}
}

func TestTheBackgroundCheckAsksAnIdleAgentWhichJobsToStopEveryCheckInterval(t *testing.T) {
	tk, w, b, p := timebox(t)
	w.bgWork = true
	setState(t, tk, "a", states.Idle, 0)
	p.since, p.haveSince, p.checkS, p.jobs = 100, true, 600, []string{"npm test"}
	b.SweepBackgroundCheck(tk, 500)
	if len(p.sent) != 0 {
		t.Fatalf("asked before the interval: %v", p.sent)
	}
	b.SweepBackgroundCheck(tk, 800)
	if len(p.sent) != 1 || !strings.Contains(p.sent[0], "1. npm test") || countEvents(t, tk, "a", "background_checked") != 1 {
		t.Fatalf("%v", p.sent)
	}
	b.SweepBackgroundCheck(tk, 900)
	if len(p.sent) != 1 {
		t.Fatalf("asked again inside the interval: %v", p.sent)
	}
	// A notice that could not be sent leaves no check behind.
	tk, w, b, p = timebox(t)
	w.bgWork, p.sendOK = true, false
	setState(t, tk, "a", states.Idle, 0)
	p.since, p.haveSince, p.checkS = 100, true, 600
	b.SweepBackgroundCheck(tk, 800)
	if countEvents(t, tk, "a", "background_checked") != 0 {
		t.Fatal("an ask that was not sent is recorded as asked")
	}
}

func TestTheStopWaitBackstopTellsTheFounderOnceWhenAnIdleAgentHasNoRecordedWait(t *testing.T) {
	tk, w, b, p := timebox(t)
	setState(t, tk, "a", states.Idle, 0)
	p.stopWait = "its task has no wait recorded"
	b.SweepStopWaitBackstop(tk, 1000)
	b.SweepStopWaitBackstop(tk, 1000+StopWaitBackstopS-1)
	if countEvents(t, tk, "a", "stop_wait_stuck") != 0 {
		t.Fatal("told before the limit")
	}
	b.SweepStopWaitBackstop(tk, 1000+StopWaitBackstopS)
	b.SweepStopWaitBackstop(tk, 1000+StopWaitBackstopS+100)
	if countEvents(t, tk, "a", "stop_wait_stuck") != 1 || !w.logged("founder told") {
		t.Fatalf("told once: %v", w.logs)
	}
}

func TestAHeadNobodyResolvedTellsTheSupervisorOnceItsNudgeScheduleIsUsedUp(t *testing.T) {
	tk, _, b, p := timebox(t)
	p.boss = "lead"
	p.forgotten = &Forgotten{HeadID: 7, IdleS: 3600, Behind: 2, Exhausted: true, Task: &db.Task{ID: 4}}
	b.SweepForgottenResolveCapped(tk, 1000)
	b.SweepForgottenResolveCapped(tk, 1100)
	if len(p.sent) != 1 || !strings.Contains(p.sent[0], "T4 (#7)") || !strings.Contains(p.sent[0], "after 3 reminders") || !strings.Contains(p.sent[0], "2 more waiting") {
		t.Fatalf("%v", p.sent)
	}
	// Blocked on record, or not yet exhausted: nothing, and the mark clears so a later head is told.
	p.forgotten = &Forgotten{HeadID: 7, Exhausted: false}
	b.SweepForgottenResolveCapped(tk, 1200)
	p.forgotten = &Forgotten{HeadID: 8, IdleS: 100, Exhausted: true}
	b.SweepForgottenResolveCapped(tk, 1300+NoticeRetryS)
	if len(p.sent) != 2 || !strings.Contains(p.sent[1], "#8") {
		t.Fatalf("%v", p.sent)
	}
}

func TestOneNoticesFailureStopsTheRestAsAnExceptionDid(t *testing.T) {
	tk, _, b, p := timebox(t)
	setState(t, tk, "a", states.Idle, 0)
	p.forgotten = &Forgotten{HeadID: 1}
	// StopWaitReason panics through the guard: Sweep returns that as its error and the later notice does not run.
	b.Ports = &panicking{p}
	if err := b.Sweep(tk, 1000); err == nil {
		t.Fatal("a failed notice was swallowed")
	}
}

type panicking struct{ *tbPorts }

func (p *panicking) StopWaitReason(db.Q, string) string { panic("boom") }

func TestATaskGivenNoTimeoutRunsFourHoursBeforeItsOpenerIsTold(t *testing.T) {
	tk, _, b, p := timebox(t)
	tid := newTask(t, tk, "a", "boss", 0)
	p.doing[tid] = true
	b.SweepTaskTimeouts(tk, 1000)
	if task, _ := db.GetTask(tk.Q, tid); task.TimeoutAt.Int64 != 1000+4*3600 {
		t.Fatalf("the default is 4h: %+v", task)
	}
	b.SweepTaskTimeouts(tk, 1000+4*3600-60)
	if len(p.sent) != 0 {
		t.Fatalf("told at 3h59m: %v", p.sent)
	}
	b.SweepTaskTimeouts(tk, 1000+4*3600+60)
	if len(p.sent) != 1 {
		t.Fatalf("not told at 4h01m: %v", p.sent)
	}
}
