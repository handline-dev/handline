package lifecycle

import (
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/usage"
	"github.com/handline/handline/go/internal/term"
)

func TestStartForWorkNeedsARepoARoomAndCountsFailuresUntilItStalls(t *testing.T) {
	m, _, _ := newManager(t)
	sp := m.Spawn.(*fakeSpawn)
	db.AddPending(m.Q, "a", "x", 1)
	if said, _ := m.StartForWork("a"); !strings.Contains(said, "set `repo:` in its agent.yaml") {
		t.Fatal(said)
	}
	m.Agents = fakeAgents{repo: "/repo"}
	if said, err := m.StartForWork("a"); err != nil || !strings.HasPrefix(said, "a: could not be started - spawn said") || strings.Contains(said, "more") {
		t.Fatalf("%q %v", said, err)
	}
	if len(sp.spawned) != 1 || sp.spawned[0] != "a@/repo" || sp.cmds[0] != "why "+StartCause {
		t.Fatalf("a start for waiting work is recorded as a start, not a launch: %v %v", sp.spawned, sp.cmds)
	}
	var told []string
	m.OnStartStall = func(_ db.Q, agent, why string) { told = append(told, agent+": "+why) }
	m.StartForWork("a")
	if len(told) != 0 {
		t.Fatalf("a failure short of the stall tells no one: %v", told)
	}
	m.StartForWork("a")
	row, _ := db.GetAgentState(m.Q, "a")
	if row.State != states.Stalled || row.WakeFailures != states.MaxFailedAttempts {
		t.Fatalf("three failed starts stall it: %+v", row)
	}
	if said, _ := m.StartForWork("a"); !strings.Contains(said, "not started again - 3 attempts have failed") {
		t.Fatal(said)
	}
	if len(told) != 1 || !strings.HasPrefix(told[0], "a: spawn said") {
		t.Fatalf("the stall is told once, with the reason: %v", told)
	}
	// A start that works clears the streak, and an agent with a worktree starts there, not in a second one.
	db.SetAgentState(m.Q, "b", states.Idle, 0, "")
	db.SetTerminal(m.Q, "b", "", "/wt-b")
	rid, _ := db.AddRequest(m.Q, db.NewRequest{Requester: "a", Target: "b", Body: "job", Kind: "task"})
	sp.ok = true
	if said, _ := m.StartForWork("b"); said != "b: started for work waiting - start-in said" || len(sp.startedIn) != 1 || sp.startedIn[0] != "b@/wt-b" {
		t.Fatalf("%q %v", said, sp.startedIn)
	}
	if last := sp.cmds[len(sp.cmds)-1]; last != "hl launch b --why 'start'" {
		t.Fatalf("the start in its own worktree records its cause: %q", last)
	}
	var got int64
	if err := m.Q.QueryRow("SELECT json_extract(payload, '$.request_id') FROM events WHERE agent = 'b' AND kind = 'started_for_work'").Scan(&got); err != nil || got != rid {
		t.Fatalf("the event names the work that was waiting: %d %v", got, err)
	}
}

func TestWakeAgentLockedWakesWithoutTakingTheTerminal(t *testing.T) {
	m, d, tm := newManager(t)
	d.waitingMsg, d.tookPrompt, tm.launchOK = "Here is your request.", true, true
	m.Typing = busyTyping{}
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.SetClosed(m.Q, "a", "sess-1")
	db.AddEvent(m.Q, "a", "Stop")
	if ok, said, _ := m.WakeAgent("a", WakeOpts{Force: true}); ok || said != TypingElsewhere+"; not waking twice" {
		t.Fatalf("a terminal in use is not typed at: %v %q", ok, said)
	}
	ok, said, err := m.WakeAgentLocked("a", WakeOpts{Force: true})
	if err != nil || !ok || said != "relaunched with the wake prompt" || len(tm.relaunched) != 1 {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	// The relaunch typed into the pane marks the worktree trusted too.
	if sp := m.Spawn.(*fakeSpawn); !slices.Equal(sp.trusted, []string{"a@/wt"}) {
		t.Fatalf("trusted %v", sp.trusted)
	}
}

type busyTyping struct{ fakeTyping }

func (busyTyping) TakeTerminal(string) (func(), bool) { return nil, false }

func TestStartForWorkHandsSpawnTheRepoExpandedAndResolved(t *testing.T) {
	m, _, _ := newManager(t)
	sp := m.Spawn.(*fakeSpawn)
	home := t.TempDir()
	t.Setenv("HANDLINE_TEST_ROOT", home)
	db.AddPending(m.Q, "a", "x", 1)
	m.Agents = fakeAgents{repo: "~/code/x"}
	m.StartForWork("a")
	want := realpath(filepath.Join(home, "code", "x"))
	if len(sp.spawned) != 1 || sp.spawned[0] != "a@"+want {
		t.Fatalf("%v, want %s", sp.spawned, want)
	}
}

func TestStartForWorkThatCannotTryTheStartIsNotCountedAgainstTheAgent(t *testing.T) {
	m, _, _ := newManager(t)
	m.Agents = fakeAgents{repo: "/repo"}
	db.AddPending(m.Q, "a", "x", 1)
	m.Spawn.(*fakeSpawn).err = syscall.EMFILE
	for i := 0; i < states.MaxFailedAttempts+1; i++ {
		if _, err := m.StartForWork("a"); !errors.Is(err, syscall.EMFILE) {
			t.Fatalf("the shortage is returned: %v", err)
		}
	}
	row, _ := db.GetAgentState(m.Q, "a")
	if row != nil && (row.State == states.Stalled || row.WakeFailures != 0) {
		t.Fatalf("a descriptor shortage must not count or stall: %+v", row)
	}
}

func TestAWakeHoldsItsSlotFromTheMomentItIsDecided(t *testing.T) {
	m, d, tm := newManager(t)
	d.waitingMsg, d.tookPrompt, tm.launchOK = "Here is your request.", true, true
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.SetClosed(m.Q, "a", "sess-1")
	db.AddEvent(m.Q, "a", "Stop")
	if ok, said, err := m.WakeAgentLocked("a", WakeOpts{Force: true}); err != nil || !ok {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	if _, p, ok, _ := db.LastEvent(m.Q, "a", "wake_reserved"); !ok || p["account"] != "resume-acct" {
		t.Fatalf("a resume reserves the account its session lives on: %v %v", ok, p)
	}
	db.SetTerminal(m.Q, "w", "t3", "/wt-w")
	db.SetClosed(m.Q, "w", "sess-w")
	db.AddEvent(m.Q, "w", "Stop")
	if ok, said, err := m.WakeAgentLocked("w", WakeOpts{Force: true, Warm: true}); err != nil || !ok {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	if _, p, ok, _ := db.LastEvent(m.Q, "w", "wake_reserved"); !ok || p["account"] != "resume-acct" {
		t.Fatalf("a warm resume reserves the account its session lives on: %v %v", ok, p)
	}
	db.SetTerminal(m.Q, "f", "t4", "/wt-f")
	db.SetClosed(m.Q, "f", "")
	db.AddEvent(m.Q, "f", "Stop")
	if ok, said, err := m.WakeAgentLocked("f", WakeOpts{Force: true}); err != nil || !ok {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	if _, p, ok, _ := db.LastEvent(m.Q, "f", "wake_reserved"); !ok || p["account"] != "room-acct" {
		t.Fatalf("a fresh start reserves where find_room points: %v %v", ok, p)
	}
	db.SetTerminal(m.Q, "b", "t2", "/wt-b")
	db.SetClosed(m.Q, "b", "sess-b")
	db.AddEvent(m.Q, "b", "Stop")
	if ok, said, err := m.WakeAgentLocked("b", WakeOpts{Force: true, MovedTo: "other"}); err != nil || !ok {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	if _, p, ok, _ := db.LastEvent(m.Q, "b", "wake_reserved"); !ok || p["account"] != "other" {
		t.Fatalf("a move holds the slot it moves to: %v %v", ok, p)
	}
}

type fullLaunch struct{ fakeLaunch }

func (fullLaunch) WakeWait(db.Q, string, *db.AgentState) *WakeWait {
	return &WakeWait{Account: "a", Window: "slot", Slot: true, Full: 2, Busy: 2, Unpaced: 2}
}
func (fullLaunch) DescribeWakeWait(w *WakeWait) string                      { return DescribeWakeWait(w) }
func (fullLaunch) SlowestPaced(db.Q, string, *db.AgentState, string) string { return "" }

// slowLaunch has a slower-paced account with room.
type slowLaunch struct{ fullLaunch }

func (slowLaunch) SlowestPaced(db.Q, string, *db.AgentState, string) string { return "slow" }

func TestAResumeOntoAFullAccountWaitsAndHoldsNothing(t *testing.T) {
	m, d, tm := newManager(t)
	d.waitingMsg, d.tookPrompt, tm.launchOK = "Here is your request.", true, true
	m.Launch = fullLaunch{}
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.SetClosed(m.Q, "a", "sess-1")
	db.AddEvent(m.Q, "a", "Stop")
	ok, said, err := m.WakeAgentLocked("a", WakeOpts{Force: true})
	if err != nil || ok || said != "waiting for a slot on a (2 of 2 mid-turn)" || len(tm.relaunched) != 0 {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	if _, _, ok, _ := db.LastEvent(m.Q, "a", "wake_reserved"); ok {
		t.Fatal("a wait reserves nothing")
	}
}

func TestARecentlyActiveResumeOntoAFullAccountMovesToWhereAnotherHasRoom(t *testing.T) {
	m, d, tm := newManager(t)
	d.waitingMsg, d.tookPrompt, tm.launchOK = "Here is your request.", true, true
	m.Launch = slowLaunch{}
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.SetClosed(m.Q, "a", "sess-1")
	db.AddEvent(m.Q, "a", "Stop") // its cache is warm: it still does not wait on a full account while another has a slot
	ok, said, err := m.WakeAgentLocked("a", WakeOpts{Force: true})
	if err != nil || !ok {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	if _, p, moved, _ := db.LastEvent(m.Q, "a", "moved_account"); !moved || p["from"] != "a" || p["to"] != "slow" {
		t.Fatalf("%v %v", moved, p)
	}
}

func TestARestartAskedToKeepTheConversationMovesOffAFullAccountWhereAnotherHasRoom(t *testing.T) {
	m, d, tm := newManager(t)
	d.waitingMsg, d.tookPrompt, tm.launchOK = "Here is your request.", true, true
	m.Launch = slowLaunch{}
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.SetClosed(m.Q, "a", "sess-1")
	ok, said, err := m.WakeAgentLocked("a", WakeOpts{Force: true, Warm: true})
	if err != nil || !ok {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	if _, p, moved, _ := db.LastEvent(m.Q, "a", "moved_account"); !moved || p["to"] != "slow" {
		t.Fatalf("when a slot frees cannot be told, so it is not waited for past the cache: %v %v", moved, p)
	}
}

// wallLaunch: the agent's own account "a" is at its 5h wall; RoomNow answers dest (why when none).
type wallLaunch struct {
	fakeLaunch
	dest, why string
}

func (wallLaunch) WakeWait(db.Q, string, *db.AgentState) *WakeWait {
	return &WakeWait{Account: "a", Pct: 99, Window: "5h"}
}
func (wallLaunch) DescribeWakeWait(w *WakeWait) string { return DescribeWakeWait(w) }
func (l wallLaunch) RoomNow(_ db.Q, _ string, _ *db.AgentState, _ string) (string, string) {
	return l.dest, l.why
}

// planHandoff answers MovePlan and keeps whether the wake asked it as a start.
type planHandoff struct {
	fakeHandoff
	dest, why string
	starting  *bool
	backoff   bool
}

func (h planHandoff) MovePlan(_ db.Q, _, _ string, starting bool) (string, string) {
	*h.starting = starting
	return h.dest, h.why
}
func (h planHandoff) BackingOff(db.Q, string) bool { return h.backoff }

func TestAPersonsStartOnAWalledPinnedAccountResumesWhereThereIsRoom(t *testing.T) {
	m, d, tm := newManager(t)
	d.waitingMsg, d.tookPrompt, tm.launchOK = "Here is your request.", true, true
	m.Launch = wallLaunch{dest: "b"}
	var asked bool
	m.Handoff = planHandoff{starting: &asked, backoff: true} // the daemon's own wake would not move; a person's does not ask
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.AddEvent(m.Q, "a", "moved_account", db.KV{K: "from", V: "b"}, db.KV{K: "to", V: "a"})
	db.SetClosed(m.Q, "a", "sess-1")
	ok, said, err := m.WakeAgentLocked("a", WakeOpts{Force: true, Person: true})
	if err != nil || !ok {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	if _, p, moved, _ := db.LastEvent(m.Q, "a", "moved_account"); !moved || p["from"] != "a" || p["to"] != "b" {
		t.Fatalf("%v %v", moved, p)
	}
	if last := tm.relaunched[len(tm.relaunched)-1]; !strings.HasSuffix(last, "-- --resume sess-1") {
		t.Fatalf("the session goes with it: %v", tm.relaunched)
	}
}

func TestAMoveOfAContextPastTheResumeLimitStartsFresh(t *testing.T) {
	m, d, tm := newManager(t)
	d.waitingMsg, d.tookPrompt, tm.launchOK = "Here is your request.", true, true
	m.Launch = slowLaunch{}
	m.Handoff = fakeHandoff{ctx: ResumeMaxContext}
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.SetClosed(m.Q, "a", "sess-1")
	db.AddEvent(m.Q, "a", "Stop")
	if ok, said, err := m.WakeAgentLocked("a", WakeOpts{Force: true}); err != nil || !ok {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	if len(tm.relaunched) != 1 || strings.Contains(tm.relaunched[0], "--resume") {
		t.Fatalf("fresh from the handoff: %v", tm.relaunched)
	}
}

func TestAPersonsStartWithNowhereToGoKeepsTheWallAndSaysWhyItDidNotMove(t *testing.T) {
	m, d, tm := newManager(t)
	d.waitingMsg, d.tookPrompt, tm.launchOK = "Here is your request.", true, true
	m.Launch = wallLaunch{why: "no account can take one more agent (b: no free slot)"}
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.SetClosed(m.Q, "a", "sess-1")
	ok, said, err := m.WakeAgentLocked("a", WakeOpts{Force: true, Person: true})
	want := DescribeWakeWait(&WakeWait{Account: "a", Pct: 99, Window: "5h"}) + "; not moved: no account can take one more agent (b: no free slot)"
	if err != nil || ok || said != want || len(tm.relaunched) != 0 {
		t.Fatalf("%v %q %v", ok, said, err)
	}
}

// olderLaunch: the agent's own account has a free slot that only an older closed agent holds.
type olderLaunch struct{ fakeLaunch }

func (olderLaunch) WakeWait(db.Q, string, *db.AgentState) *WakeWait {
	return &WakeWait{Account: "a", Window: "slot", Slot: true, Full: 2, Busy: 1, Older: 1, Unpaced: 2}
}
func (olderLaunch) DescribeWakeWait(w *WakeWait) string                      { return DescribeWakeWait(w) }
func (olderLaunch) SlowestPaced(db.Q, string, *db.AgentState, string) string { return "" }

// pacedLaunch: the agent's own account has a free slot that only its weekly pace holds.
type pacedLaunch struct{ fakeLaunch }

func (pacedLaunch) WakeWait(db.Q, string, *db.AgentState) *WakeWait {
	return &WakeWait{Account: "a", Window: "slot", Slot: true, Full: 0, Busy: 1, Unpaced: 2, Pace: &usage.Pace{State: usage.PaceOver, Line: 40, Used: 60}}
}
func (pacedLaunch) DescribeWakeWait(w *WakeWait) string                      { return DescribeWakeWait(w) }
func (pacedLaunch) SlowestPaced(db.Q, string, *db.AgentState, string) string { return "" }

func TestTheDaemonsWakeWaitsForThePaceAndAPersonsStartDoesNot(t *testing.T) {
	m, d, tm := newManager(t)
	d.waitingMsg, d.tookPrompt, tm.launchOK = "Here is your request.", true, true
	m.Launch = pacedLaunch{}
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.SetClosed(m.Q, "a", "sess-1")
	d.head = &db.Request{Requester: "boss", Target: "a"}
	if ok, said, err := m.WakeAgentLocked("a", WakeOpts{}); err != nil || ok || !strings.Contains(said, "weekly pace") {
		t.Fatalf("the daemon's wake for a peer's work waits for the pace: %v %q %v", ok, said, err)
	}
	d.head.Requester = db.Human
	if ok, said, err := m.WakeAgentLocked("a", WakeOpts{}); err != nil || !ok {
		t.Fatalf("a person's message does not: %v %q %v", ok, said, err)
	}
	db.SetClosed(m.Q, "a", "sess-1")
	if ok, said, err := m.WakeAgentLocked("a", WakeOpts{Force: true, Person: true}); err != nil || !ok {
		t.Fatalf("a person's start does not: %v %q %v", ok, said, err)
	}
	db.SetClosed(m.Q, "a", "sess-1")
	d.head.Requester = "boss"
	if ok, said, err := m.WakeAgentLocked("a", WakeOpts{Force: true, RestartedBy: "boss"}); err != nil || !ok {
		t.Fatalf("a restart does not: %v %q %v", ok, said, err)
	}
	if _, _, moved, _ := db.LastEvent(m.Q, "a", "moved_account"); moved {
		t.Fatal("it starts on its own account")
	}
}

// pacedOlderLaunch: the pace shrinks the cap to none and an older waiter fills the rest of what the cap would be without it.
type pacedOlderLaunch struct{ fakeLaunch }

func (pacedOlderLaunch) WakeWait(db.Q, string, *db.AgentState) *WakeWait {
	return &WakeWait{Account: "a", Window: "slot", Slot: true, Full: 0, Busy: 1, Older: 1, Unpaced: 2}
}
func (pacedOlderLaunch) DescribeWakeWait(w *WakeWait) string                      { return DescribeWakeWait(w) }
func (pacedOlderLaunch) SlowestPaced(db.Q, string, *db.AgentState, string) string { return "" }

func TestAPersonsStartIsHeldToTheCapWithoutThePace(t *testing.T) {
	m, d, tm := newManager(t)
	d.waitingMsg, d.tookPrompt, tm.launchOK = "Here is your request.", true, true
	m.Launch = pacedOlderLaunch{}
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.SetClosed(m.Q, "a", "sess-1")
	if ok, said, err := m.WakeAgentLocked("a", WakeOpts{Force: true, Person: true}); err != nil || !ok {
		t.Fatalf("one of two slots is busy without the pace, and a person queues behind no older waiter: %v %q %v", ok, said, err)
	}
	if _, _, moved, _ := db.LastEvent(m.Q, "a", "moved_account"); moved {
		t.Fatal("it starts on its own account")
	}
}

func TestAPersonsStartDoesNotQueueBehindOlderClosedAgents(t *testing.T) {
	m, d, tm := newManager(t)
	d.waitingMsg, d.tookPrompt, tm.launchOK = "Here is your request.", true, true
	m.Launch = olderLaunch{}
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.SetClosed(m.Q, "a", "sess-1")
	if ok, _, err := m.WakeAgentLocked("a", WakeOpts{Force: true}); err != nil || ok {
		t.Fatalf("the daemon's wake waits its turn: %v %v", ok, err)
	}
	if ok, said, err := m.WakeAgentLocked("a", WakeOpts{Force: true, Person: true}); err != nil || !ok {
		t.Fatalf("a person's start does not: %v %q %v", ok, said, err)
	}
	if _, _, moved, _ := db.LastEvent(m.Q, "a", "moved_account"); moved {
		t.Fatal("it starts on its own account")
	}
}

func TestADaemonWakeOnAWalledAccountAsksThePlanAsAStart(t *testing.T) {
	m, d, tm := newManager(t)
	d.waitingMsg, d.tookPrompt, tm.launchOK = "Here is your request.", true, true
	var starting bool
	m.Launch = wallLaunch{}
	m.Handoff = planHandoff{dest: "b", starting: &starting}
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.SetClosed(m.Q, "a", "sess-1")
	ok, said, err := m.WakeAgentLocked("a", WakeOpts{Force: true, Warm: true})
	if err != nil || !ok || !starting {
		t.Fatalf("a warm restart asks the plan as a start, and follows it: %v %q %v %v", ok, said, err, starting)
	}
	if _, p, moved, _ := db.LastEvent(m.Q, "a", "moved_account"); !moved || p["to"] != "b" {
		t.Fatalf("%v %v", moved, p)
	}
}

func TestAColdResumeOntoAFullAccountMovesToTheSlowestPacedOne(t *testing.T) {
	m, d, tm := newManager(t)
	d.waitingMsg, d.tookPrompt, tm.launchOK = "Here is your request.", true, true
	m.Launch = slowLaunch{}
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.SetClosed(m.Q, "a", "sess-1") // no activity on record: its cache is cold
	ok, said, err := m.WakeAgentLocked("a", WakeOpts{Force: true})
	if err != nil || !ok {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	if _, p, moved, _ := db.LastEvent(m.Q, "a", "moved_account"); !moved || p["from"] != "a" || p["to"] != "slow" {
		t.Fatalf("a cold wake on a full account moves: %v %v", moved, p)
	}
	if _, p, held, _ := db.LastEvent(m.Q, "a", "wake_reserved"); !held || p["account"] != "slow" {
		t.Fatalf("and holds the slot it moved to: %v %v", held, p)
	}
}

func TestAColdWakeUnderTheResumeLimitResumes(t *testing.T) {
	for _, ctx := range []int64{0, ResumeMaxContext - 1} {
		m, d, tm := newManager(t)
		d.waitingMsg, d.tookPrompt, tm.launchOK = "Here is your request.", true, true
		m.Handoff = fakeHandoff{ctx: ctx}
		db.SetTerminal(m.Q, "a", "t1", "/wt")
		db.SetClosed(m.Q, "a", "sess-1") // no activity on record: its cache is cold
		if ok, said, err := m.WakeAgentLocked("a", WakeOpts{Force: true}); err != nil || !ok {
			t.Fatalf("%v %q %v", ok, said, err)
		}
		if len(tm.relaunched) != 1 || !strings.HasSuffix(tm.relaunched[0], "-- --resume sess-1") {
			t.Fatalf("context %d: resumed, not started fresh: %q", ctx, tm.relaunched)
		}
		if _, p, _, _ := db.LastEvent(m.Q, "a", "wake"); p["cache_expired"] != nil {
			t.Fatalf("not recorded as a cold start: %v", p)
		}
	}
}

func TestAColdWakeIsHandedWhatWokeItAsItsFirstPrompt(t *testing.T) {
	m, d, tm := newManager(t)
	d.waitingMsg, d.tookPrompt, tm.launchOK = "Here is your request.", true, true
	m.Handoff = fakeHandoff{ctx: ResumeMaxContext} // too big to read again
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.SetClosed(m.Q, "a", "sess-1") // no activity on record: its cache is cold
	if ok, said, err := m.WakeAgentLocked("a", WakeOpts{Force: true}); err != nil || !ok {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	if len(tm.relaunched) != 1 || strings.Contains(tm.relaunched[0], "--resume") {
		t.Fatalf("one fresh launch: %q", tm.relaunched)
	}
	text, _ := os.ReadFile(filepath.Join(m.Home, "wake", "a.txt"))
	if !strings.HasSuffix(string(text), "\n\nHere is your request.") {
		t.Fatalf("not left for a first turn spent on the handoff: %q", text)
	}
	if len(d.told) != 1 || d.told[0] != "" {
		t.Fatalf("told once, to the new session: %q", d.told)
	}
}

func TestAColdResumeWithNowhereToGoWaitsForItsSlot(t *testing.T) {
	m, d, tm := newManager(t)
	d.waitingMsg, d.tookPrompt, tm.launchOK = "Here is your request.", true, true
	m.Launch = fullLaunch{} // every other account is full too
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.SetClosed(m.Q, "a", "sess-1")
	if ok, said, err := m.WakeAgentLocked("a", WakeOpts{Force: true}); err != nil || ok || said != "waiting for a slot on a (2 of 2 mid-turn)" {
		t.Fatalf("%v %q %v", ok, said, err)
	}
}

func TestAColdResumeWhoseOwnAccountIsFullOnlyByOlderWaitersDoesNotMoveToItself(t *testing.T) {
	m, d, tm := newManager(t)
	d.waitingMsg, d.tookPrompt, tm.launchOK = "Here is your request.", true, true
	m.Launch = fullLaunch{} // the account is full, and no other account has a free slot
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.SetClosed(m.Q, "a", "sess-1")
	if ok, _, err := m.WakeAgentLocked("a", WakeOpts{Force: true}); err != nil || ok {
		t.Fatalf("it waits: %v %v", ok, err)
	}
	if _, p, moved, _ := db.LastEvent(m.Q, "a", "moved_account"); moved {
		t.Fatalf("and is not moved, least of all to the account it is on: %v", p)
	}
}

// roomLaunch has room on every account but "full".
type roomLaunch struct{ fakeLaunch }

func (roomLaunch) RoomOn(_ db.Q, _ string, _ *db.AgentState, acct string) string {
	if acct == "full" {
		return "has no free slot"
	}
	return ""
}

func TestAClosedAgentMovedByHandResumesOnTheNewAccountOnce(t *testing.T) {
	m, d, tm := newManager(t)
	d.waitingMsg, d.tookPrompt, tm.launchOK = "Here is your request.", true, true
	m.Launch = roomLaunch{}
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.SetClosed(m.Q, "a", "sess-1")
	db.AddEvent(m.Q, "a", "Stop")
	db.AddEvent(m.Q, "a", MoveOwedKind, db.KV{K: "to", V: "full"}, db.KV{K: "by", V: "the founder"})
	ok, said, err := m.WakeAgentLocked("a", WakeOpts{Force: true})
	if err != nil || ok || said != "full has no free slot - waiting to start a there (moved by the founder)" || len(tm.relaunched) != 0 {
		t.Fatalf("a destination with no room waits: %v %q %v", ok, said, err)
	}
	db.AddEvent(m.Q, "a", MoveOwedKind, db.KV{K: "to", V: "beta"}, db.KV{K: "by", V: "the founder"})
	tm.launchOK = false
	if ok, _, err := m.WakeAgentLocked("a", WakeOpts{Force: true}); err != nil || ok {
		t.Fatalf("a launch that fails: %v %v", ok, err)
	}
	tm.launchOK = true
	if ok, said, err := m.WakeAgentLocked("a", WakeOpts{Force: true}); err != nil || !ok {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	if _, p, moved, _ := db.LastEvent(m.Q, "a", "moved_account"); !moved || p["to"] != "beta" || p["trigger"] != "forced" {
		t.Fatalf("the move is on the record: %v %v", moved, p)
	}
	var n int
	m.Q.QueryRow("SELECT COUNT(*) FROM events WHERE agent = 'a' AND kind = 'moved_account'").Scan(&n)
	if n != 1 {
		t.Fatalf("once across the retry, not %d times", n)
	}
	if _, p, held, _ := db.LastEvent(m.Q, "a", "wake_reserved"); !held || p["account"] != "beta" {
		t.Fatalf("it starts on the account it was moved to: %v %v", held, p)
	}
	if last := tm.relaunched[len(tm.relaunched)-1]; !strings.HasSuffix(last, "-- --resume sess-1") {
		t.Fatalf("the session goes with it: %v", tm.relaunched)
	}
	db.SetClosed(m.Q, "a", "sess-2")
	row, _ := db.GetAgentState(m.Q, "a")
	if m.moveOwed("a", row) != nil {
		t.Fatal("the wake used the move up: the next one picks as usual")
	} // A session that comes up with no wake recorded (a trust screen, a hand relaunch) and is closed again: the move was for the close
	// before.
	db.AddEvent(m.Q, "a", MoveOwedKind, db.KV{K: "to", V: "beta"}, db.KV{K: "by", V: "the founder"})
	db.ClearClosed(m.Q, "a")
	db.SetClosed(m.Q, "a", "sess-3")
	m.Q.Exec("UPDATE agent_state SET closed_at = closed_at + 5 WHERE agent = 'a'")
	row, _ = db.GetAgentState(m.Q, "a")
	if m.moveOwed("a", row) != nil {
		t.Fatal("a close after the move voids it")
	}
}

// lateObserve lists the agent's session only once a launch has been typed, as a resume that is still booting when the wait ends.
type lateObserve struct {
	tm  *fakeTerm
	pid int
}

func (l lateObserve) LiveSessions() (map[string]int, bool) {
	if len(l.tm.relaunched) == 0 {
		return map[string]int{}, true
	}
	return map[string]int{"a": l.pid}, true
}

func TestAResumeThatComesUpAfterTheWaitIsNotLaunchedOverFresh(t *testing.T) {
	m, d, tm := newManager(t)
	d.waitingMsg, d.tookPrompt, tm.launchOK = "Here is your request.", false, false
	m.Observe, m.TermRun = lateObserve{tm, 77}, &fakeTermRun{pid: 77, status: "idle"}
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.SetClosed(m.Q, "a", "sess-1")
	db.AddEvent(m.Q, "a", "Stop")
	ok, said, _ := m.WakeAgent("a", WakeOpts{Force: true})
	if !ok || len(tm.relaunched) != 1 {
		t.Fatalf("%v %q launches %v", ok, said, tm.relaunched)
	}
}

func TestARelaunchIsToldAsALaunchOnceItsLineIsTyped(t *testing.T) {
	m, d, tm := newManager(t)
	d.waitingMsg, d.tookPrompt, tm.launchOK = "Here is your request.", true, true
	var told []string
	m.OnLaunch = func(agent string) {
		if _, _, ok, _ := db.LastEvent(m.Q, agent, "launching"); !ok {
			t.Error("told before the launching event was recorded")
		}
		told = append(told, agent)
	}
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.SetClosed(m.Q, "a", "sess-1")
	if ok, said, err := m.WakeAgentLocked("a", WakeOpts{Force: true}); err != nil || !ok {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	if len(told) != 1 || told[0] != "a" {
		t.Fatalf("told %q", told)
	}
}

// firstTurn is a terminal host where a launch typed at a shell draws a session that is mid-turn until done closes: the turn a woken
// session runs on its wake prompt.
type firstTurn struct {
	mu       sync.Mutex
	launched map[string]bool
	done     chan struct{}
}

func (*firstTurn) Name() string                   { return "fake" }
func (*firstTurn) Available() bool                { return true }
func (*firstTurn) ListTerminals() map[string]bool { return map[string]bool{"t1": true, "t2": true} }
func (f *firstTurn) ReadScreen(handle string, _ int) *term.Screen {
	f.mu.Lock()
	defer f.mu.Unlock()
	if !f.launched[handle] {
		return &term.Screen{Tail: []string{"~/work $ "}, Source: "screen"}
	}
	rule := strings.Repeat("─", 60)
	return &term.Screen{Tail: []string{"✻ Working… (esc to interrupt)", rule, "│ > │", rule}, Source: "screen"}
}
func (f *firstTurn) Send(handle, _ string, enter bool) (bool, string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if enter {
		f.launched[handle] = true
	}
	return true, ""
}
func (f *firstTurn) WaitIdle(string, int, func([]string) bool) bool { <-f.done; return true }

// hostRelaunch is fakeTerm with the real Relaunch, over a host of the test's own.
type hostRelaunch struct {
	*fakeTerm
	t *term.Term
}

func (h hostRelaunch) Relaunch(handle, cmd string, started func() bool) (bool, string) {
	return h.t.Relaunch(handle, cmd, started)
}

func TestASecondClosedAgentIsWokenWhileTheFirstOnesFirstTurnRuns(t *testing.T) {
	m, d, tm := newManager(t)
	d.waitingMsg, d.tookPrompt = "Here is your request.", true
	host := &firstTurn{launched: map[string]bool{}, done: make(chan struct{})}
	defer close(host.done)
	tt := term.New(host, nil)
	tt.Sleep = func(time.Duration) {}
	m.Term = hostRelaunch{tm, tt}
	db.SetTerminal(m.Q, "a", "t1", "/wt/a")
	db.SetTerminal(m.Q, "b", "t2", "/wt/b")
	db.SetClosed(m.Q, "a", "sess-a")
	db.SetClosed(m.Q, "b", "sess-b")
	woke := make(chan string, 2)
	go func() {
		for _, agent := range []string{"a", "b"} {
			if ok, said, err := m.WakeAgentLocked(agent, WakeOpts{Force: true}); !ok || err != nil {
				woke <- agent + ": " + said
				return
			}
			woke <- agent
		}
	}()
	for _, want := range []string{"a", "b"} {
		select {
		case got := <-woke:
			if got != want {
				t.Fatalf("woke %q, want %q", got, want)
			}
		case <-time.After(10 * time.Second):
			t.Fatalf("%s's wake waited on a first turn", want)
		}
	}
	if len(d.told) != 2 {
		t.Fatalf("each told once: %q", d.told)
	}
}

// tryTyping is the terminal lock as it is: whoever holds it, nobody else gets it until it is released.
type tryTyping struct{ mu sync.Mutex }

func (l *tryTyping) TakeTerminal(string) (func(), bool) {
	if !l.mu.TryLock() {
		return nil, false
	}
	return l.mu.Unlock, true
}

// slowSpawn opens a terminal whose session is slow to come up: StartIn waits on up, then says no session ran in time.
type slowSpawn struct {
	fakeSpawn
	q      db.Q
	up     chan struct{}
	mu     sync.Mutex
	opened []string
}

func (s *slowSpawn) StartIn(agent, wt, _ string) (bool, string, error) {
	s.mu.Lock()
	handle := "%" + strconv.Itoa(len(s.opened)+1)
	s.opened = append(s.opened, handle)
	s.mu.Unlock()
	db.SetTerminal(s.q, agent, handle, wt)
	<-s.up
	return false, agent + ": started in " + wt + " (terminal " + handle + ")\n  but no session is running in it after 20s", nil
}

// Seen live: every message waiting for a closed agent asked for a start while the first one's session was still coming up, each opened a
// terminal of its own, and three slow starts stalled an agent that came up after all.
func TestASlowStartOpensOneTerminalWhoeverElseAsksMeanwhile(t *testing.T) {
	m, _, _ := newManager(t)
	m.Agents = fakeAgents{repo: "/repo"}
	sp := &slowSpawn{q: m.Q, up: make(chan struct{})}
	m.Spawn, m.Typing = sp, &tryTyping{}
	db.AddPending(m.Q, "a", "x", 1)
	db.SetAgentState(m.Q, "a", states.Idle, 0, "")
	db.SetTerminal(m.Q, "a", "", "/wt-a")
	first := make(chan string)
	go func() { said, _ := m.StartForWork("a"); first <- said }()
	for {
		sp.mu.Lock()
		n := len(sp.opened)
		sp.mu.Unlock()
		if n == 1 {
			break
		}
		time.Sleep(time.Millisecond)
	}
	for range 2 {
		if said, _ := m.StartForWork("a"); said != "a: a start or wake is under way - not starting twice" {
			t.Fatalf("a second start while the first comes up: %q", said)
		}
	}
	close(sp.up)
	<-first
	if said, _ := m.StartForWork("a"); said != "a: started already - not starting twice" {
		t.Fatalf("a start after the first opened its terminal: %q", said)
	}
	row, _ := db.GetAgentState(m.Q, "a")
	if len(sp.opened) != 1 || row.State == states.Stalled || row.WakeFailures != 1 {
		t.Fatalf("one terminal and no stall: %v %+v", sp.opened, row)
	}
}

// A recorded worktree that is gone is made again from its branch and started in; one that cannot be is a failed start that says why,
// never a fresh worktree in its place.
func TestAGoneWorktreeIsMadeAgainOrTheStartFailsWithWhy(t *testing.T) {
	m, _, _ := newManager(t)
	sp := m.Spawn.(*fakeSpawn)
	m.Agents = fakeAgents{repo: "/repo"}
	db.AddPending(m.Q, "a", "x", 1)
	db.SetTerminal(m.Q, "a", "", "/wt-a")
	sp.gone, sp.recreate, sp.ok = map[string]bool{"/wt-a": true}, "/wt-a", true
	if said, _ := m.StartForWork("a"); !strings.HasPrefix(said, "a: started for work waiting") || len(sp.startedIn) != 1 || sp.startedIn[0] != "a@/wt-a" {
		t.Fatalf("made again and started in: %q %v", said, sp.startedIn)
	}
	db.SetTerminal(m.Q, "b", "", "/wt-b")
	db.AddPending(m.Q, "b", "x", 1)
	sp.gone["/wt-b"], sp.recreate, sp.recreateWhy = true, "", "b: its worktree /wt-b is gone and its branch wt-b is not in /repo"
	if said, _ := m.StartForWork("b"); !strings.Contains(said, "its branch wt-b is not in /repo") || len(sp.spawned) != 0 {
		t.Fatalf("a failed start that says why, no fresh worktree: %q %v", said, sp.spawned)
	}
	if row, _ := db.GetAgentState(m.Q, "b"); row.WakeFailures != 1 {
		t.Fatalf("it counts toward the stall that tells the lead: %+v", row)
	}
}

func TestTheLoadHoldsTheDaemonsOwnStartsButNotAPersons(t *testing.T) {
	m, _, _ := newManager(t)
	m.LoadHeld = func(string) string { return "held: machine load - load 2.00 per core, limit 1.50" }
	m.Agents = fakeAgents{repo: "/repo"}
	sp := m.Spawn.(*fakeSpawn)
	db.AddPending(m.Q, "a", "x", 1)
	if said, _ := m.StartForWork("a"); !strings.Contains(said, "a: not started - held: machine load") || len(sp.spawned) != 0 {
		t.Fatalf("a start for work went past the load: %q %v", said, sp.spawned)
	}
	db.SetTerminal(m.Q, "b", "t1", "/wt")
	db.SetClosed(m.Q, "b", "sess-1")
	db.SetAgentState(m.Q, "b", states.Idle, 0, "")
	db.AddPending(m.Q, "b", "hi", 1)
	if ok, said, _ := m.WakeAgent("b", WakeOpts{}); ok || !strings.Contains(said, "held: machine load") {
		t.Fatalf("the daemon's wake went past the load: %v %q", ok, said)
	}
	if row, _ := db.GetAgentState(m.Q, "b"); row.WakeFailures != 0 {
		t.Fatalf("a held wake counted as a failure: %d", row.WakeFailures)
	}
	for _, o := range []WakeOpts{{Person: true}, {Force: true}} {
		if _, said, _ := m.WakeAgent("b", o); strings.Contains(said, "held: machine load") {
			t.Fatalf("%+v: a person's wake or a restart was held: %q", o, said)
		}
	}
}

// The harbor case: closed at 23:07, the founder's message at 23:11, and every wake held by the machine's load until 23:26. A person's
// message is never held: the start or wake for it goes, and the pass is recorded once for that message.
func TestTheLoadNeverHoldsAPersonsMessage(t *testing.T) {
	m, d, _ := newManager(t)
	m.LoadHeld = func(string) string { return "held: machine load - load 5.40 per core, limit 1.50" }
	m.Agents = fakeAgents{repo: "/repo"}
	sp := m.Spawn.(*fakeSpawn)
	passes := func(agent string) int {
		var n int
		if err := m.Q.QueryRow("SELECT COUNT(*) FROM events WHERE agent = ? AND kind = 'load_limit_passed'", agent).Scan(&n); err != nil {
			t.Fatal(err)
		}
		return n
	}
	d.head = &db.Request{ID: 7, TS: m.now(), Requester: "lead", Target: "harbor"}
	if said, _ := m.StartForWork("harbor"); !strings.Contains(said, "not started - held: machine load") || passes("harbor") != 0 {
		t.Fatalf("an agent's message went past the load: %q", said)
	}
	d.head = &db.Request{ID: 8, TS: m.now(), Requester: db.Human, Target: "harbor"}
	for range 2 {
		if said, _ := m.StartForWork("harbor"); strings.Contains(said, "held: machine load") {
			t.Fatalf("the founder's message was held: %q", said)
		}
	}
	if passes("harbor") != 1 || len(sp.spawned)+len(sp.startedIn) == 0 {
		t.Fatalf("one pass for one message, and a start: %d %v %v", passes("harbor"), sp.spawned, sp.startedIn)
	}
	d.head = &db.Request{ID: 10, TS: m.now(), Requester: db.Human, Target: "harbor"}
	if m.StartForWork("harbor"); passes("harbor") != 2 {
		t.Fatalf("the next message is a pass of its own: %d", passes("harbor"))
	}
	db.SetTerminal(m.Q, "b", "t1", "/wt")
	db.SetClosed(m.Q, "b", "sess-1")
	db.SetAgentState(m.Q, "b", states.Idle, 0, "")
	d.head = &db.Request{ID: 9, TS: m.now(), Requester: db.Human, Target: "b"}
	if _, said, _ := m.WakeAgent("b", WakeOpts{}); strings.Contains(said, "held: machine load") || passes("b") != 1 {
		t.Fatalf("the daemon's wake for the founder's message was held: %q", said)
	}
}
