package lifecycle

import (
	"errors"
	"path/filepath"
	"strings"
	"syscall"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
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
	if len(sp.spawned) != 1 || sp.spawned[0] != "a@/repo" {
		t.Fatalf("%v", sp.spawned)
	}
	m.StartForWork("a")
	m.StartForWork("a")
	row, _ := db.GetAgentState(m.Q, "a")
	if row.State != states.Stalled || row.WakeFailures != states.MaxFailedAttempts {
		t.Fatalf("three failed starts stall it: %+v", row)
	}
	if said, _ := m.StartForWork("a"); !strings.Contains(said, "not started again - 3 attempts have failed") {
		t.Fatal(said)
	}
	// A start that works clears the streak, and an agent with a worktree starts there, not in a second one.
	db.SetAgentState(m.Q, "b", states.Idle, 0, "")
	db.SetTerminal(m.Q, "b", "", "/wt-b")
	sp.ok = true
	if said, _ := m.StartForWork("b"); said != "b: started for work waiting - start-in said" || len(sp.startedIn) != 1 || sp.startedIn[0] != "b@/wt-b" {
		t.Fatalf("%q %v", said, sp.startedIn)
	}
	if _, ok, _ := db.LastEventID(m.Q, "b", "started_for_work"); !ok {
		t.Fatal("no event")
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
}

type busyTyping struct{ fakeTyping }

func (busyTyping) TakeTerminal(string) (func(), bool) { return nil, false }

func TestWakeInPlaceTypesAPointerAtTheHandoffAndSpendsIt(t *testing.T) {
	m, _, tm := newManager(t)
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.SetHandoff(m.Q, "a", "left off here")
	tm.busy = true
	if ok, why := m.wakeInPlace("a", "t1", WakeOpts{}); ok || !strings.Contains(why, "busy or has a modal open") || len(tm.resumedWith) != 0 {
		t.Fatalf("%v %q", ok, why)
	}
	tm.busy = false
	ok, why := m.wakeInPlace("a", "t1", WakeOpts{})
	if !ok || why != "typed the pointer" || len(tm.resumedWith) != 1 || !strings.Contains(tm.resumedWith[0], "wake/a.handoff.txt") {
		t.Fatalf("%v %q %v", ok, why, tm.resumedWith)
	}
	if h, _ := db.GetHandoff(m.Q, "a"); h != "" {
		t.Fatalf("the handoff is spent once typed: %q", h)
	}
}

func TestStartForWorkHandsSpawnTheRepoExpandedAndResolved(t *testing.T) {
	m, _, _ := newManager(t)
	sp := m.Spawn.(*fakeSpawn)
	home := t.TempDir()
	t.Setenv("HOME", home)
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
	return &WakeWait{Account: "a", Window: "slot", Slot: true, Full: 2, Busy: 2}
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

func TestAWarmResumeOntoAFullAccountWaitsEvenWhereAnotherHasRoom(t *testing.T) {
	m, d, tm := newManager(t)
	d.waitingMsg, d.tookPrompt, tm.launchOK = "Here is your request.", true, true
	m.Launch = slowLaunch{}
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.SetClosed(m.Q, "a", "sess-1")
	db.AddEvent(m.Q, "a", "Stop") // its cache is warm: the resume is free, so it waits for its slot
	ok, said, err := m.WakeAgentLocked("a", WakeOpts{Force: true})
	if err != nil || ok || said != "waiting for a slot on a (2 of 2 mid-turn)" || len(tm.relaunched) != 0 {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	if _, _, moved, _ := db.LastEvent(m.Q, "a", "moved_account"); moved {
		t.Fatal("a warm wake is not moved")
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

func TestAClosedAgentMovedByHandWakesFreshOnTheNewAccountOnce(t *testing.T) {
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
	if last := tm.relaunched[len(tm.relaunched)-1]; strings.Contains(last, "--resume") {
		t.Fatalf("a fresh start, not a resume of the old account's session: %v", tm.relaunched)
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
