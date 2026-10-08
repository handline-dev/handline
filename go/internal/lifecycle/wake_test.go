package lifecycle

import (
	"path/filepath"
	"strings"
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
	if ok, said, _ := m.WakeAgent("a", WakeOpts{Force: true}); ok || said != "held by someone" {
		t.Fatalf("a held terminal is not typed at: %v %q", ok, said)
	}
	ok, said, err := m.WakeAgentLocked("a", WakeOpts{Force: true})
	if err != nil || !ok || said != "relaunched with the wake prompt" || len(tm.relaunched) != 1 {
		t.Fatalf("%v %q %v", ok, said, err)
	}
}

type busyTyping struct{ fakeTyping }

func (busyTyping) HeldByOther(db.Q, string) string { return "someone" }
func (busyTyping) HeldWhy(string, string) string   { return "held by someone" }

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
