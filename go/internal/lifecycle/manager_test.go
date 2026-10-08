package lifecycle

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/term"
)

type fakeDelivery struct {
	Delivery
	idle       int64
	sessionUp  bool
	gone       bool
	waitingMsg string
	tookPrompt bool
}

func (f *fakeDelivery) BgHolds(db.Q, string, int64) (bool, error)       { return false, nil }
func (f *fakeDelivery) BgOwesNothing(db.Q, string) (bool, error)        { return false, nil }
func (f *fakeDelivery) BgMayStop(db.Q, string, int64) (bool, error)     { return false, nil }
func (f *fakeDelivery) IdleSince(db.Q, string) (int64, bool, error)     { return f.idle, f.idle != 0, nil }
func (f *fakeDelivery) EffectiveHead(db.Q, string) (*db.Request, error) { return nil, nil }
func (f *fakeDelivery) TypeGate(db.Q, string, string, bool, bool) (string, error) {
	return "", nil
}
func (f *fakeDelivery) MarkTyped(db.Q, string, map[string]any) error { return nil }
func (f *fakeDelivery) SessionPresent(string) (bool, error)          { return f.sessionUp, nil }
func (f *fakeDelivery) WaitSessionGone(string) bool                  { return f.gone }
func (f *fakeDelivery) SessionStillDrawn(string, bool) bool          { return false }
func (f *fakeDelivery) PastDialog(db.Q, string) (string, error)      { return "", nil }
func (f *fakeDelivery) EnforceEdges(db.Q, string) (bool, error)      { return true, nil }
func (f *fakeDelivery) WaitingPrompt(db.Q, string) (string, func(string) error, bool, error) {
	return f.waitingMsg, func(string) error { return nil }, f.waitingMsg != "", nil
}
func (f *fakeDelivery) WaitForSession(string, int, string) bool { return f.tookPrompt }

type fakeHandoff struct{ HandoffPort }

func (fakeHandoff) AnsweredTurn(db.Q, string) bool                 { return false }
func (fakeHandoff) Capture(db.Q, string) (string, []string, error) { return "record", nil, nil }
func (fakeHandoff) Record(db.Q, string, string) ([]string, error)  { return nil, nil }
func (fakeHandoff) ClearNote(db.Q, string)                         {}
func (fakeHandoff) CacheClock(db.Q, string, string) (int64, int64, bool, bool) {
	return 0, 0, false, false
}
func (fakeHandoff) PreviousSession(db.Q, string, string) (string, string) { return "s0", "/t/s0.jsonl" }
func (fakeHandoff) RequestLine(db.Q, string) string                       { return "Your request: none." }
func (fakeHandoff) MemoryLine(string) string                              { return "" }

type fakeTerm struct {
	Terminal
	relaunched  []string
	busy        bool
	resumedWith []string
	launchOK    bool
	closedPane  bool
}

func (f *fakeTerm) TypeableNow(string) bool { return !f.busy }
func (f *fakeTerm) ResumeWith(_, p string) (bool, string) {
	f.resumedWith = append(f.resumedWith, p)
	return true, "typed the pointer"
}
func (*fakeTerm) Available() bool                       { return true }
func (*fakeTerm) WaitIdle(string) bool                  { return true }
func (*fakeTerm) ScreenBlocker(string) *term.Blocker    { return nil }
func (*fakeTerm) BackgroundWork(string) (string, bool)  { return "", true }
func (*fakeTerm) CloseSession(string) (bool, string)    { return true, "" }
func (*fakeTerm) BypassPromptShowing(string) bool       { return false }
func (*fakeTerm) TrustPromptShowing(string) bool        { return false }
func (*fakeTerm) DriverScope() func()                   { return func() {} }
func (f *fakeTerm) CloseTerminal(string) (bool, string) { f.closedPane = true; return true, "" }
func (f *fakeTerm) Relaunch(_, cmd string, started func() bool) (bool, string) {
	f.relaunched = append(f.relaunched, cmd)
	if f.launchOK && started() {
		return true, ""
	}
	return false, "nothing started"
}

type fakeLaunch struct{ Launch }

func (fakeLaunch) Drifted(string, *db.AgentState) bool             { return false }
func (fakeLaunch) WakeWait(db.Q, string, *db.AgentState) *WakeWait { return nil }
func (fakeLaunch) ChooseAccount(db.Q, string) bool                 { return true }
func (fakeLaunch) CommandFor(agent, _, why, _ string) (string, error) {
	return "hl launch " + agent + " --why '" + why + "'", nil
}

type fakeSpawn struct {
	Spawner
	startedIn, spawned []string
	ok                 bool
}

func (*fakeSpawn) WorktreePath(w string) string { return w }
func (f *fakeSpawn) StartIn(agent, wt, _ string) (bool, string) {
	f.startedIn = append(f.startedIn, agent+"@"+wt)
	return f.ok, "start-in said"
}
func (f *fakeSpawn) Spawn(agent, repo string) (bool, string) {
	f.spawned = append(f.spawned, agent+"@"+repo)
	return f.ok, "spawn said\nmore"
}

type fakeAgents struct {
	AgentsPort
	repo string
}

func (a fakeAgents) RepoOf(string) string             { return a.repo }
func (fakeAgents) ReadConfig(string) error            { return nil }
func (fakeAgents) CurrentAccount(db.Q, string) string { return "acct" }

type fakeStatus struct{}

func (fakeStatus) ReadForAgent(string, string) SessionStatus {
	return SessionStatus{SessionID: "sess-1", CtxPct: 12}
}

type fakeObserve struct{ sessions map[string]int }

func (f fakeObserve) LiveSessions() (map[string]int, bool) { return f.sessions, true }

type fakeTyping struct{}

func (fakeTyping) TakeTerminal(string) (func(), bool) { return func() {}, true }
func (fakeTyping) HeldByOther(db.Q, string) string    { return "" }
func (fakeTyping) HeldWhy(string, string) string      { return "" }

func newManager(t *testing.T) (*Manager, *fakeDelivery, *fakeTerm) {
	t.Helper()
	home := t.TempDir()
	d := &fakeDelivery{idle: 1, sessionUp: true, gone: true}
	tm := &fakeTerm{}
	m := &Manager{
		Q: openDB(t), Home: home, Log: func(string, ...any) {}, Settings: &Settings{Home: home},
		Delivery: d, Handoff: fakeHandoff{}, Term: tm, Launch: fakeLaunch{}, Spawn: &fakeSpawn{}, Agents: fakeAgents{},
		Status: fakeStatus{}, Observe: fakeObserve{sessions: map[string]int{}}, Typing: fakeTyping{},
		Now: func() int64 { return int64(time.Now().Unix()) }, Sleep: func(time.Duration) {},
	}
	return m, d, tm
}

func TestShouldCloseNeedsIdleLongEnoughAndASessionToResume(t *testing.T) {
	m, d, _ := newManager(t)
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	row, _ := db.GetAgentState(m.Q, "a")
	now := m.now()
	d.idle = now - 10
	if yes, err := m.ShouldClose("a", states.Idle, row, now, "sess"); err != nil || yes {
		t.Fatalf("recent activity closed it: %v %v", yes, err)
	}
	d.idle = now - states.IdleCloseS - 1
	if yes, _ := m.ShouldClose("a", states.Idle, row, now, ""); yes {
		t.Fatal("closed with no session to resume")
	}
	if yes, _ := m.ShouldClose("a", states.Running, row, now, "sess"); yes {
		t.Fatal("closed a working agent")
	}
	if yes, err := m.ShouldClose("a", states.Idle, row, now, "sess"); err != nil || !yes {
		t.Fatalf("idle past its window was kept: %v %v", yes, err)
	}
}

func TestClosingRecordsTheResumeAndClosesThePane(t *testing.T) {
	m, _, tm := newManager(t)
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	ok, said, err := m.CloseAgent("a", SessionStatus{SessionID: "sess-1"}, CloseOpts{})
	if err != nil || !ok || !strings.Contains(said, "terminal closed") {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	row, _ := db.GetAgentState(m.Q, "a")
	if !isClosed(row) || row.ResumeSession.String != "sess-1" || row.Terminal.String != "" || row.Worktree.String != "/wt" || !tm.closedPane {
		t.Fatalf("%+v", row)
	}
	if h, _ := db.GetHandoff(m.Q, "a"); h != "record" {
		t.Fatalf("handoff %q", h)
	}
}

func TestACloseWithNoSessionIdIsRefusedUnlessItDiscardsOnPurpose(t *testing.T) {
	m, _, _ := newManager(t)
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	if ok, said, _ := m.CloseAgent("a", SessionStatus{}, CloseOpts{}); ok || !strings.Contains(said, "no session id") {
		t.Fatalf("%v %q", ok, said)
	}
	if ok, _, _ := m.CloseAgent("a", SessionStatus{}, CloseOpts{Discard: true}); !ok {
		t.Fatal("a fresh start was refused")
	}
}

func TestAClosedAgentIsWokenByResumingItsSessionAndTheStreakResets(t *testing.T) {
	m, d, tm := newManager(t)
	d.waitingMsg = "Here is your request."
	d.tookPrompt = true
	tm.launchOK = true
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.SetClosed(m.Q, "a", "sess-1")
	db.AddEvent(m.Q, "a", "Stop") // a warm cache: it did something just now
	db.BumpCounter(m.Q, "a", "wake_failures", 2)
	// the sweep's view: no process in the table, nothing drawn
	ok, said, err := m.WakeAgent("a", WakeOpts{Force: true})
	if err != nil || !ok || said != "relaunched with the wake prompt" {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	if len(tm.relaunched) != 1 || !strings.HasSuffix(tm.relaunched[0], "-- --resume sess-1") {
		t.Fatalf("%v", tm.relaunched)
	}
	row, _ := db.GetAgentState(m.Q, "a")
	if isClosed(row) || row.WakeFailures != 0 {
		t.Fatalf("%+v", row)
	}
	if _, ok, _ := db.LastEventID(m.Q, "a", "wake"); !ok {
		t.Fatal("no wake event")
	}
	if _, err := os.Stat(filepath.Join(m.Home, "wake", "a.txt")); err != nil {
		t.Fatalf("the wake prompt file was not written: %v", err)
	}
}

func TestThreeWakesThatStartNothingStallTheAgent(t *testing.T) {
	m, d, tm := newManager(t)
	d.waitingMsg = "work"
	tm.launchOK = false
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.SetClosed(m.Q, "a", "sess-1")
	db.AddEvent(m.Q, "a", "Stop")
	for i := 0; i < states.MaxFailedAttempts; i++ {
		if ok, _, err := m.WakeAgent("a", WakeOpts{Force: true}); ok || err != nil {
			t.Fatalf("round %d: %v %v", i, ok, err)
		}
	}
	row, _ := db.GetAgentState(m.Q, "a")
	if row.State != states.Stalled || !isClosed(row) || row.ResumeSession.String != "sess-1" {
		t.Fatalf("%+v", row)
	}
}

func TestAWakeWithNothingWaitingIsNotSpent(t *testing.T) {
	m, _, _ := newManager(t)
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.SetClosed(m.Q, "a", "sess-1")
	if ok, said, _ := m.WakeAgent("a", WakeOpts{}); ok || said != NothingWaiting {
		t.Fatalf("%v %q", ok, said)
	}
}

func TestWakePromptByCase(t *testing.T) {
	m, _, _ := newManager(t)
	got, _ := m.WakePrompt("a", true, WakePromptIn{RestartNote: "Restarted.", Waiting: "msg"})
	if got != "Restarted.\n\nmsg" {
		t.Fatalf("%q", got)
	}
	got, _ = m.WakePrompt("a", false, WakePromptIn{MovedTo: "beta"})
	if !strings.Contains(got, "started on beta") || strings.Contains(got, "Your request") {
		t.Fatalf("%q", got)
	}
	got, _ = m.WakePrompt("a", false, WakePromptIn{WasClosed: true})
	if !strings.Contains(got, "There is no handoff") || !strings.Contains(got, "recorded at /t/s0.jsonl") {
		t.Fatalf("%q", got)
	}
}

func TestARestartOwedIsFinishedByTheNextWakeAndBacksOff(t *testing.T) {
	m, _, _ := newManager(t)
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.SetClosed(m.Q, "a", "sess-1")
	db.AddEvent(m.Q, "a", RestartOwedKind, db.KV{K: "by", V: "human"}, db.KV{K: "fresh", V: false})
	row, _ := db.GetAgentState(m.Q, "a")
	if owed, _ := m.RestartOwed("a", row); owed == nil {
		t.Fatal("not owed")
	}
	if c, _ := m.ClosedWakeCause("a", row); c == nil || c.Kind != "restart" {
		t.Fatalf("%+v", c)
	}
	db.AddEvent(m.Q, "a", "wake_failed", db.KV{K: "why", V: "boom"})
	db.BumpCounter(m.Q, "a", "wake_failures", 1)
	row, _ = db.GetAgentState(m.Q, "a")
	if w, _ := m.RestartRetryWait("a", row, m.now()); w <= 0 || w > WakeBackoffS[0] {
		t.Fatalf("wait %d", w)
	}
	if note, _ := m.RestartRetryNote("a", row); note != "down, retry 2/3: boom" {
		t.Fatalf("%q", note)
	}
	db.AddEvent(m.Q, "a", "wake")
	if owed, _ := m.RestartOwed("a", row); owed != nil {
		t.Fatal("still owed after a wake")
	}
}
