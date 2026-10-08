package lifecycle

import (
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/session"
	"github.com/handline/handline/go/internal/states"
)

// fakeRunner is the structured sessions: what was asked of them, and the process each agent has.
type fakeRunner struct {
	alive, busy  map[string]bool
	opened       []StartSpec
	sent         []string // agent|op|text
	closed       []string
	interrupted  []string
	retrying     map[string]int64
	ledger       map[string]bool // ops answered from the record, as S1's per-agent ledger does
	openErr      []error         // one per Open, consumed in order; nil entries and an empty list succeed
	sendErr      error
	closeErr     error
	idleAfterAll bool
}

func newRunner() *fakeRunner { return &fakeRunner{alive: map[string]bool{}, busy: map[string]bool{}} }

func (f *fakeRunner) Structured(string) bool { return true }
func (f *fakeRunner) Open(s StartSpec) error {
	f.opened = append(f.opened, s)
	if len(f.openErr) > 0 {
		err := f.openErr[0]
		f.openErr = f.openErr[1:]
		if err != nil {
			return err
		}
	}
	f.alive[s.Agent] = true
	return nil
}
func (f *fakeRunner) Alive(a string) bool { return f.alive[a] }
func (f *fakeRunner) Busy(a string) bool  { return f.busy[a] }
func (f *fakeRunner) Send(a, op, text string) error {
	if f.ledger == nil {
		f.ledger = map[string]bool{}
	}
	if f.ledger[a+"|"+op] {
		return nil // a repeat is answered with the recorded receipt and written no more
	}
	if f.sendErr != nil {
		return f.sendErr
	}
	f.ledger[a+"|"+op] = true
	f.sent = append(f.sent, a+"|"+op+"|"+text)
	return nil
}
func (f *fakeRunner) Interrupt(a string) error     { f.interrupted = append(f.interrupted, a); return nil }
func (f *fakeRunner) RetryingSince(a string) int64 { return f.retrying[a] }
func (f *fakeRunner) Close(a string) error {
	f.closed = append(f.closed, a)
	if f.closeErr != nil {
		return f.closeErr
	}
	f.alive[a] = false
	return nil
}
func (f *fakeRunner) WaitIdle(a string, _ time.Duration) bool { return !f.busy[a] || f.idleAfterAll }

func structuredManager(t *testing.T) (*Manager, *fakeRunner, *fakeDelivery, *fakeTerm) {
	m, d, tm := newManager(t)
	r := newRunner()
	m.Run = r
	n := 0
	m.NewID = func() string { n++; return fmt.Sprintf("new-id-%d", n) }
	return m, r, d, tm
}

func TestAStructuredStartRecordsTheSessionAndSendsTheFirstMessage(t *testing.T) {
	q := openDB(t)
	r := newRunner()
	s := &Sessions{Q: q, Run: r, Launch: fakeLaunch{}, NewID: func() string { return "new-id" }}
	id, err := s.Start("a", "/wt", StartOpts{Cause: "start", Prompt: "do it"})
	if err != nil || id != "new-id" {
		t.Fatalf("%q %v", id, err)
	}
	if len(r.opened) != 1 || r.opened[0] != (StartSpec{Agent: "a", Worktree: "/wt", Account: "room-acct", SessionID: "new-id"}) {
		t.Fatalf("%+v", r.opened)
	}
	if len(r.sent) != 1 || r.sent[0] != "a|start:new-id|do it" { // NewID is fixed here, so the op is too
		t.Fatalf("%v", r.sent)
	}
	row, _ := db.GetAgentState(q, "a")
	if row.Terminal.String != "" || row.Worktree.String != "/wt" || row.ResumeSession.String != "new-id" || row.Account.String != "room-acct" {
		t.Fatalf("%+v", row)
	}
	var cause string
	var resumed int
	if err := q.QueryRow("SELECT cause, resumed FROM session_starts WHERE agent = 'a'").Scan(&cause, &resumed); err != nil || cause != "start" || resumed != 0 {
		t.Fatalf("%q %d %v", cause, resumed, err)
	}
	if _, err := s.Start("a", "/wt", StartOpts{}); err == nil {
		t.Fatal("a second process was opened for a running agent")
	}
}

func TestAStructuredStartThatCannotOpenOrCannotDeliverRecordsNothing(t *testing.T) {
	q := openDB(t)
	r := newRunner()
	r.openErr = []error{errors.New("signed out")}
	s := &Sessions{Q: q, Run: r, Launch: fakeLaunch{}}
	if _, err := s.Start("a", "/wt", StartOpts{Prompt: "x"}); err == nil || !strings.Contains(err.Error(), "signed out") {
		t.Fatalf("%v", err)
	}
	r.sendErr = errors.New("never echoed")
	if _, err := s.Start("a", "/wt", StartOpts{Prompt: "x"}); err == nil || !strings.Contains(err.Error(), "first message was not delivered") {
		t.Fatalf("%v", err)
	}
	if len(r.closed) != 1 || r.alive["a"] {
		t.Fatalf("the session that took no message was left running: %v", r.closed)
	}
	if row, _ := db.GetAgentState(q, "a"); row != nil {
		t.Fatalf("%+v", row)
	}
}

func TestAClosedStructuredAgentIsWokenByResumingItsSession(t *testing.T) {
	m, r, d, tm := structuredManager(t)
	d.waitingMsg = "Here is your request."
	db.SetTerminal(m.Q, "a", "", m.Home)
	db.SetClosed(m.Q, "a", "sess-1")
	db.AddEvent(m.Q, "a", "Stop") // a warm cache
	db.BumpCounter(m.Q, "a", "wake_failures", 2)
	ok, said, err := m.WakeAgent("a", WakeOpts{Force: true})
	if err != nil || !ok || said != "started with the wake prompt" {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	if len(r.opened) != 1 || !r.opened[0].Resume || r.opened[0].SessionID != "sess-1" || r.opened[0].Account != "resume-acct" || r.opened[0].Worktree != m.Home {
		t.Fatalf("%+v", r.opened)
	}
	if len(r.sent) != 1 || !strings.HasSuffix(r.sent[0], "Here is your request.") {
		t.Fatalf("%v", r.sent)
	}
	// the same session resumed again is told again: the ledger outlives the session
	r.alive["a"] = false
	db.SetClosed(m.Q, "a", "sess-1")
	db.AddEvent(m.Q, "a", "Stop")
	if ok, _, _ := m.WakeAgent("a", WakeOpts{Force: true}); !ok || len(r.sent) != 2 || !strings.HasSuffix(r.sent[1], "Here is your request.") {
		t.Fatalf("%v %v", ok, r.sent)
	}
	row, _ := db.GetAgentState(m.Q, "a")
	if isClosed(row) || row.WakeFailures != 0 || row.ResumeSession.String != "sess-1" {
		t.Fatalf("%+v", row)
	}
	if _, ok, _ := db.LastEventID(m.Q, "a", "wake"); !ok {
		t.Fatal("no wake event")
	}
	if len(tm.relaunched) != 0 {
		t.Fatal("the terminal was used")
	}
}

func TestAResumeThatFailsFallsBackToAFreshSessionAndThreeFailuresStall(t *testing.T) {
	m, r, d, _ := structuredManager(t)
	d.waitingMsg = "work"
	db.SetTerminal(m.Q, "a", "", m.Home)
	db.SetClosed(m.Q, "a", "sess-1")
	db.AddEvent(m.Q, "a", "Stop")
	r.openErr = []error{errors.New("no such session")}
	if ok, _, err := m.WakeAgent("a", WakeOpts{Force: true}); !ok || err != nil {
		t.Fatalf("%v %v", ok, err)
	}
	if len(r.opened) != 2 || !r.opened[0].Resume || r.opened[1].Resume || !strings.HasPrefix(r.opened[1].SessionID, "new-id") {
		t.Fatalf("%+v", r.opened)
	}
	// every start fails now
	db.SetClosed(m.Q, "a", "sess-1")
	r.alive["a"] = false
	for i := 0; i < states.MaxFailedAttempts; i++ {
		r.openErr = []error{errors.New("boom"), errors.New("boom")}
		if ok, said, _ := m.WakeAgent("a", WakeOpts{Force: true}); ok || said != "boom" {
			t.Fatalf("round %d: %v %q", i, ok, said)
		}
	}
	row, _ := db.GetAgentState(m.Q, "a")
	if row.State != states.Stalled || !isClosed(row) || row.ResumeSession.String != "sess-1" {
		t.Fatalf("%+v", row)
	}
}

func TestAStructuredWakeWithNothingWaitingOrNoWorktreeStartsNothing(t *testing.T) {
	m, r, _, _ := structuredManager(t)
	db.SetTerminal(m.Q, "a", "", m.Home)
	db.SetClosed(m.Q, "a", "sess-1")
	if ok, said, _ := m.WakeAgent("a", WakeOpts{}); ok || said != NothingWaiting {
		t.Fatalf("%v %q", ok, said)
	}
	db.SetTerminal(m.Q, "b", "", "")
	db.SetClosed(m.Q, "b", "sess-2")
	if ok, said, _ := m.WakeAgent("b", WakeOpts{Force: true}); ok || said != "no worktree to wake in" {
		t.Fatalf("%v %q", ok, said)
	}
	if len(r.opened) != 0 {
		t.Fatal("opened")
	}
}

func TestALiveStructuredAgentIsSentItsHandoff(t *testing.T) {
	m, r, _, tm := structuredManager(t)
	db.SetTerminal(m.Q, "a", "", m.Home)
	db.SetHandoff(m.Q, "a", "the record")
	if ok, said, _ := m.WakeAgent("a", WakeOpts{}); ok || !strings.Contains(said, "no session process") {
		t.Fatalf("%v %q", ok, said)
	}
	r.alive["a"], r.busy["a"] = true, true
	if ok, said, _ := m.WakeAgent("a", WakeOpts{}); ok || !strings.Contains(said, "mid-turn") {
		t.Fatalf("%v %q", ok, said)
	}
	r.busy["a"] = false
	ok, said, err := m.WakeAgent("a", WakeOpts{})
	if err != nil || !ok || said != "handoff sent" {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	if len(r.sent) != 1 || !strings.Contains(r.sent[0], "a|wake:") || !strings.Contains(r.sent[0], "handoff.txt") {
		t.Fatalf("%v", r.sent)
	}
	if h, _ := db.GetHandoff(m.Q, "a"); h != "" {
		t.Fatalf("handoff kept: %q", h)
	}
	if len(tm.resumedWith) != 0 {
		t.Fatal("the terminal was typed at")
	}
}

func TestClosingAStructuredAgentRecordsTheResumeAndKeepsNoPane(t *testing.T) {
	m, r, _, tm := structuredManager(t)
	db.SetTerminal(m.Q, "a", "", "/wt")
	r.alive["a"] = true
	ok, said, err := m.CloseAgent("a", SessionStatus{SessionID: "sess-1"}, CloseOpts{})
	if err != nil || !ok || said != "a: session closed" {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	row, _ := db.GetAgentState(m.Q, "a")
	if !isClosed(row) || row.ResumeSession.String != "sess-1" || row.Worktree.String != "/wt" || r.alive["a"] || tm.closedPane {
		t.Fatalf("%+v", row)
	}
	if h, _ := db.GetHandoff(m.Q, "a"); h != "record" {
		t.Fatalf("handoff %q", h)
	}
	if ok, said, _ := m.CloseAgent("a", SessionStatus{SessionID: "sess-1"}, CloseOpts{}); ok || !strings.Contains(said, "nothing to close") {
		t.Fatalf("%v %q", ok, said)
	}
}

func TestAStructuredCloseThatLeavesAProcessIsNotReportedStopped(t *testing.T) {
	m, r, _, _ := structuredManager(t)
	db.SetTerminal(m.Q, "a", "", "/wt")
	r.alive["a"] = true
	r.closeErr = errors.New("pid 7 is still alive")
	ok, said, _ := m.CloseAgent("a", SessionStatus{SessionID: "sess-1"}, CloseOpts{})
	if ok || !strings.Contains(said, "would not end") {
		t.Fatalf("%v %q", ok, said)
	}
	if row, _ := db.GetAgentState(m.Q, "a"); isClosed(row) {
		t.Fatal("recorded closed over a live process")
	}
	if _, ok, _ := db.LastEventTS(m.Q, "a", "close_failed"); !ok {
		t.Fatal("no close_failed")
	}
	if h, _ := db.GetHandoff(m.Q, "a"); h != "" {
		t.Fatalf("a failed close left its handoff: %q", h)
	}
}

func TestAMidTurnStructuredAgentIsNotClosedUnlessForcedAfterTheTurn(t *testing.T) {
	m, r, _, _ := structuredManager(t)
	db.SetTerminal(m.Q, "a", "", "/wt")
	r.alive["a"], r.busy["a"] = true, true
	if ok, said, _ := m.CloseAgent("a", SessionStatus{SessionID: "s"}, CloseOpts{}); ok || !strings.Contains(said, "mid-turn") {
		t.Fatalf("%v %q", ok, said)
	}
	row, _ := db.GetAgentState(m.Q, "a")
	now := m.now()
	m.Delivery.(*fakeDelivery).idle = now - states.IdleCloseS - 1
	if yes, _ := m.ShouldClose("a", states.Idle, row, now, "s"); yes {
		t.Fatal("ShouldClose wanted to close a session mid-turn")
	}
	db.SetAgentState(m.Q, "a", states.Running, 0, "")
	if ok, said, _ := m.CloseAgent("a", SessionStatus{SessionID: "s"}, CloseOpts{Force: true}); ok || !strings.Contains(said, "still mid-turn") {
		t.Fatalf("%v %q", ok, said)
	}
	r.idleAfterAll = true
	if ok, said, err := m.CloseAgent("a", SessionStatus{SessionID: "s"}, CloseOpts{Force: true}); !ok {
		t.Fatalf("%v %q %v", ok, said, err)
	}
}

func TestRestartingAStructuredAgentClosesAndOpensItsSessionAgain(t *testing.T) {
	m, r, _, _ := structuredManager(t)
	db.SetTerminal(m.Q, "a", "", m.Home)
	db.AddEvent(m.Q, "a", "Stop")
	r.alive["a"] = true
	ok, said, err := m.RestartAgent("a", SessionStatus{SessionID: "sess-1"}, RestartOpts{By: "human"})
	if err != nil || !ok || !strings.Contains(said, "restarted, resuming its conversation") {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	if len(r.closed) != 1 || len(r.opened) != 1 || !r.opened[0].Resume || r.opened[0].SessionID != "sess-1" {
		t.Fatalf("closed %v opened %+v", r.closed, r.opened)
	}
	if len(r.sent) != 1 || !strings.Contains(r.sent[0], "Restarted by human") {
		t.Fatalf("the restart was not told: %v", r.sent)
	}
	// fresh: a new session id and the old one forgotten
	r.openErr = nil
	ok, said, _ = m.RestartAgent("a", SessionStatus{SessionID: "sess-1"}, RestartOpts{By: "human", Fresh: true})
	if !ok || !strings.Contains(said, "as a new conversation") || r.opened[1].Resume || !strings.HasPrefix(r.opened[1].SessionID, "new-id") {
		t.Fatalf("%v %q %+v", ok, said, r.opened)
	}
}

func TestInterruptStopsARunningTurnOnly(t *testing.T) {
	m, r, _, _ := structuredManager(t)
	if ok, said, _ := m.InterruptAgent("a"); ok || !strings.Contains(said, "no running session") {
		t.Fatalf("%v %q", ok, said)
	}
	r.alive["a"] = true
	if ok, said, _ := m.InterruptAgent("a"); ok || !strings.Contains(said, "not mid-turn") {
		t.Fatalf("%v %q", ok, said)
	}
	r.busy["a"] = true
	if ok, _, err := m.InterruptAgent("a"); !ok || err != nil || len(r.interrupted) != 1 {
		t.Fatalf("%v %v %v", ok, err, r.interrupted)
	}
	if _, ok, _ := db.LastEventTS(m.Q, "a", "interrupted"); !ok {
		t.Fatal("no event")
	}
}

func TestTheTickKnowsAStructuredAgentByItsProcessNotItsTerminal(t *testing.T) {
	m, r, _, _ := structuredManager(t)
	db.SetTerminal(m.Q, "a", "", m.Home)
	db.SetClosed(m.Q, "a", "sess-1")
	row, _ := db.GetAgentState(m.Q, "a")
	if !m.ResumableWithoutTerminal(row) {
		t.Fatal("a closed structured agent with a worktree is woken")
	}
	db.AddEvent(m.Q, "a", "Stop")
	db.SetHandoff(m.Q, "a", "h")
	if kind, _, err := m.StartOrWakeNeeded("a", row); err != nil || kind != "" {
		t.Fatalf("%q %v", kind, err)
	}
	if m.TriggersApplyTo("a", row) {
		t.Fatal("triggers on a closed agent")
	}
	db.ClearClosed(m.Q, "a")
	row, _ = db.GetAgentState(m.Q, "a")
	if m.TriggersApplyTo("a", row) {
		t.Fatal("triggers with no process")
	}
	r.alive["a"] = true
	if !m.TriggersApplyTo("a", row) {
		t.Fatal("no triggers on a live session")
	}
}

func TestStartForWorkAndSpawnOpenAStructuredSessionWithoutATerminal(t *testing.T) {
	wt := t.TempDir()
	q := openDB(t)
	r := newRunner()
	s := &Spawns{Q: q, Host: &fakeHost{}, Launch: fakeLaunch{}, Delivery: upDelivery{}, Run: r, NewID: func() string { return "new-id" }}
	ok, said, err := s.StartIn("a", wt, "")
	if err != nil || !ok || !strings.Contains(said, "a: started in "+wt) || strings.Contains(said, "terminal") {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	if row, _ := db.GetAgentState(q, "a"); row.Terminal.String != "" || row.Worktree.String != wt || row.ResumeSession.String != "new-id" {
		t.Fatalf("%+v", row)
	}
	r.alive["a"] = false
	ok, said, err = s.SpawnWith("a", "/repo", SpawnOpts{Prompt: "first task"})
	if err != nil || !ok || !strings.Contains(said, "first task delivered") {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	if len(r.sent) != 1 || !strings.HasSuffix(r.sent[0], "|first task") {
		t.Fatalf("%v", r.sent)
	}
	r.alive["a"] = false
	r.openErr = []error{errors.New("signed out")}
	if ok, said, _ := s.SpawnWith("a", "/repo", SpawnOpts{}); ok || !strings.Contains(said, "starting the agent failed: signed out") {
		t.Fatalf("%v %q", ok, said)
	}
	if ok, said, _ := s.SpawnWith("a", "/repo", SpawnOpts{DryRun: true}); !ok || !strings.Contains(said, "would start in its own worktree") {
		t.Fatalf("%v %q", ok, said)
	}
}

func structuredRec(t *testing.T) (*Reconciler, *fakeRunner, *[]string) {
	r, logs := newRec(t, map[string]session.Observed{})
	f := newRunner()
	r.Ports.Run = f
	return r, f, logs
}

func TestASweepRecordsAnAgentWhoseProcessIsGoneClosedWithItsStoredSession(t *testing.T) {
	r, f, _ := structuredRec(t)
	db.SetTerminal(r.Q, "a", "", "/wt")
	db.SetResumeSession(r.Q, "a", "sess-9")
	db.SetAgentState(r.Q, "a", states.Running, 0, "")
	var acts []string
	if err := r.Sweep(&acts); err != nil {
		t.Fatal(err)
	}
	row, _ := db.GetAgentState(r.Q, "a")
	if !isClosed(row) || row.ResumeSession.String != "sess-9" || row.State != states.Idle || len(acts) != 1 {
		t.Fatalf("%+v %v", row, acts)
	}
	// it came back without the record knowing
	f.alive["a"] = true
	db.BumpCounter(r.Q, "a", "wake_failures", states.MaxFailedAttempts)
	db.SetAgentState(r.Q, "a", states.Stalled, 0, "3 wakes started no session")
	acts = nil
	r.Sweep(&acts)
	row, _ = db.GetAgentState(r.Q, "a")
	if isClosed(row) || row.State != states.Idle || row.WakeFailures != 0 || len(acts) != 2 {
		t.Fatalf("%+v %v", row, acts)
	}
}

func TestASweepLeavesAnAgentThatWasNeverStartedAlone(t *testing.T) {
	r, _, _ := structuredRec(t)
	db.SetAgentState(r.Q, "a", states.Idle, 0, "registered")
	var acts []string
	r.Sweep(&acts)
	if row, _ := db.GetAgentState(r.Q, "a"); isClosed(row) || len(acts) != 0 {
		t.Fatalf("%+v %v", row, acts)
	}
}

func TestASilentRunningStructuredAgentIsInterruptedWhileATurnRunsAndRelabelledWhenNone(t *testing.T) {
	r, f, _ := structuredRec(t) // now is 5000
	db.SetTerminal(r.Q, "a", "", "/wt")
	db.SetAgentState(r.Q, "a", states.Running, 0, "")
	f.alive["a"] = true
	var acts []string
	db.AddEventAt(r.Q, 4990, "a", "Stop")
	r.Sweep(&acts)
	if len(f.interrupted) != 0 || len(acts) != 0 {
		t.Fatal("a recent frame was treated as silence")
	}
	r.Q.Exec("DELETE FROM events WHERE agent = 'a'")
	db.AddEventAt(r.Q, 1, "a", "Stop")
	f.busy["a"] = true
	r.Sweep(&acts)
	if len(f.interrupted) != 1 || len(acts) != 1 || !strings.Contains(acts[0], "turn interrupted") {
		t.Fatalf("%v %v", f.interrupted, acts)
	}
	if row, _ := db.GetAgentState(r.Q, "a"); row.State != states.Running {
		t.Fatal("relabelled while a turn runs")
	}
	f.busy["a"] = false
	acts = nil
	r.Sweep(&acts)
	if row, _ := db.GetAgentState(r.Q, "a"); row.State != states.Idle || len(acts) != 1 || !strings.Contains(acts[0], "back to IDLE") {
		t.Fatalf("%+v %v", row, acts)
	}
}

func TestReconcileWakesADueClosedStructuredAgentThatHasNoTerminal(t *testing.T) {
	r, _, _ := structuredRec(t)
	db.SetTerminal(r.Q, "a", "", "/wt")
	db.SetClosed(r.Q, "a", "sess-1")
	db.SetAgentState(r.Q, "a", states.Parked, 4000, "")
	db.SetClosed(r.Q, "a", "sess-1")
	var woke []string
	r.Ports.EnforceEdges = func(db.Q, string) error { return nil }
	r.Ports.WaitingFor = func(db.Q, string) (bool, error) { return true, nil }
	r.Ports.WakeAgent = func(_ db.Q, a string) (bool, string, error) { woke = append(woke, a); return true, "", nil }
	r.Ports.QueuedOrIdle = func(db.Q, string, string) (string, error) { return states.Idle, nil }
	if _, err := r.Reconcile(); err != nil {
		t.Fatal(err)
	}
	if len(woke) != 1 {
		t.Fatalf("%v", woke)
	}
}

func TestATurnThatOnlyRetriesTheModelApiIsInterruptedPastTheLimit(t *testing.T) {
	r, f, _ := structuredRec(t) // now is 5000
	db.SetTerminal(r.Q, "a", "", "/wt")
	db.SetAgentState(r.Q, "a", states.Running, 0, "")
	f.alive["a"], f.busy["a"] = true, true
	f.retrying = map[string]int64{"a": 5000 - APIRetryLimitS + 1}
	var acts []string
	r.Sweep(&acts)
	if len(f.interrupted) != 0 {
		t.Fatal("interrupted inside the limit")
	}
	f.retrying["a"] = 5000 - APIRetryLimitS
	r.Sweep(&acts)
	if len(f.interrupted) != 1 || len(acts) != 1 || !strings.Contains(acts[0], "model API") {
		t.Fatalf("%v %v", f.interrupted, acts)
	}
	if _, ok, _ := db.LastEventTS(r.Q, "a", "api_unreachable"); !ok {
		t.Fatal("no event")
	}
	if row, _ := db.GetAgentState(r.Q, "a"); row.FailedAttempts != 0 || row.State == states.Stalled {
		t.Fatalf("counted against the agent: %+v", row)
	}
}

func TestAFirstMessageThatFailsAndACloseThatFailsKeepsBothErrors(t *testing.T) {
	r := newRunner()
	r.sendErr = errors.New("never echoed")
	r.closeErr = errors.New("pid 7 still alive")
	s := &Sessions{Q: openDB(t), Run: r, Launch: fakeLaunch{}}
	_, err := s.Start("a", "/wt", StartOpts{Prompt: "x"})
	if err == nil || !strings.Contains(err.Error(), "never echoed") || !strings.Contains(err.Error(), "pid 7 still alive") {
		t.Fatalf("%v", err)
	}
}

func TestASpawnWaitsForTheLockAWakeHolds(t *testing.T) {
	wt := t.TempDir()
	r := newRunner()
	locks := &AgentLocks{}
	s := &Spawns{Q: openDB(t), Host: &fakeHost{}, Launch: fakeLaunch{}, Delivery: upDelivery{}, Run: r, Locks: locks}
	release := locks.Lock("a")
	done := make(chan struct{})
	go func() { s.StartIn("a", wt, ""); close(done) }()
	select {
	case <-done:
		t.Fatal("the spawn did not wait for the lock")
	case <-time.After(100 * time.Millisecond):
	}
	if len(r.opened) != 0 {
		t.Fatal("opened under the lock")
	}
	release()
	<-done
	if len(r.opened) != 1 {
		t.Fatalf("%+v", r.opened)
	}
}

func TestATurnInterruptedJustNowIsNotInterruptedAgain(t *testing.T) {
	r, f, _ := structuredRec(t) // now is 5000
	db.SetTerminal(r.Q, "a", "", "/wt")
	db.SetAgentState(r.Q, "a", states.Running, 0, "")
	f.alive["a"], f.busy["a"] = true, true
	db.AddEventAt(r.Q, 1, "a", "Stop")
	db.AddEventAt(r.Q, 5000-InterruptHoldS+1, "a", "interrupted")
	var acts []string
	r.Sweep(&acts)
	if len(f.interrupted) != 0 || len(acts) != 0 {
		t.Fatalf("%v %v", f.interrupted, acts)
	}
	f.retrying = map[string]int64{"a": 1}
	r.Sweep(&acts)
	if len(f.interrupted) != 0 {
		t.Fatal("the retry stall interrupted inside the hold")
	}
	r.Q.Exec("DELETE FROM events WHERE kind = 'interrupted'")
	db.AddEventAt(r.Q, 5000-InterruptHoldS, "a", "interrupted")
	r.Sweep(&acts)
	if len(f.interrupted) != 1 {
		t.Fatalf("%v", f.interrupted)
	}
}
