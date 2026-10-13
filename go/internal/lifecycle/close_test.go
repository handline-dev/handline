package lifecycle

import (
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
)

func TestCloseFreshEndsTheSessionAndForgetsTheOneItWouldResume(t *testing.T) {
	m, _, _ := newManager(t)
	if ok, said, _ := m.CloseFresh("ghost", SessionStatus{}, CloseOpts{}); ok || !strings.Contains(said, "has never been started") {
		t.Fatalf("%v %q", ok, said)
	}
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	ok, said, err := m.CloseFresh("a", SessionStatus{SessionID: "sess-1"}, CloseOpts{KeepPane: true})
	if err != nil || !ok || !strings.Contains(said, "its next wake starts a new session") {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	row, _ := db.GetAgentState(m.Q, "a")
	if !isClosed(row) || row.ResumeSession.String != "" {
		t.Fatalf("%+v", row)
	}
	if _, ok, _ := db.LastEventID(m.Q, "a", "fresh_close"); !ok {
		t.Fatal("no fresh_close event")
	}
	// Already closed: the mark stays, only the id goes, and the record is taken for the new start.
	db.SetAgentState(m.Q, "b", states.Idle, 0, "")
	db.SetClosed(m.Q, "b", "sess-2")
	if ok, _, err := m.CloseFresh("b", SessionStatus{}, CloseOpts{}); err != nil || !ok {
		t.Fatalf("%v %v", ok, err)
	}
	if row, _ := db.GetAgentState(m.Q, "b"); !isClosed(row) || row.ResumeSession.String != "" {
		t.Fatalf("%+v", row)
	}
}

func TestEndSessionClosesWhatIsThereAndRefusesWhatIsNot(t *testing.T) {
	m, d, _ := newManager(t)
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	d.sessionUp = false
	if ok, why, err := m.EndSession("a", "sess-1", SessionStatus{}, true, false); err != nil || ok || !strings.Contains(why, "no session process") {
		t.Fatalf("%v %q %v", ok, why, err)
	}
	d.sessionUp = true
	if ok, why, err := m.EndSession("a", "sess-1", SessionStatus{SessionID: "sess-1"}, true, false); err != nil || !ok || why != "a: session closed, terminal kept" {
		t.Fatalf("%v %q %v", ok, why, err)
	}
	if row, _ := db.GetAgentState(m.Q, "a"); !isClosed(row) || row.ResumeSession.String != "sess-1" {
		t.Fatalf("%+v", row)
	}
	if ok, why, _ := m.EndSession("none", "", SessionStatus{}, true, false); ok || why != "no terminal to close" {
		t.Fatalf("%v %q", ok, why)
	}
}

func TestACacheIsExpiredWhenNothingHasHappenedForTheWarmWindow(t *testing.T) {
	m, _, _ := newManager(t)
	now := m.now()
	if yes, _ := m.CacheExpired("a", "", now); yes {
		t.Fatal("no session: nothing to expire")
	}
	if yes, _ := m.CacheExpired("a", "s", now); !yes {
		t.Fatal("no clock at all counts as cold")
	}
	db.AddEventAt(m.Q, now-10, "a", "Stop")
	if yes, _ := m.CacheExpired("a", "s", now); yes {
		t.Fatal("it answered ten seconds ago")
	}
	if yes, _ := m.CacheExpired("a", "s", now+states.ResumeWarmS+10); !yes {
		t.Fatal("warm past the window")
	}
}

func TestWhatIsOwedAClosedAgentWakesIt(t *testing.T) {
	m, _, _ := newManager(t)
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.SetClosed(m.Q, "a", "sess-1")
	db.SetAgentState(m.Q, "a", states.Idle, 0, "")
	row, _ := db.GetAgentState(m.Q, "a")
	if kind, _, err := m.StartOrWakeNeeded("a", row); err != nil || kind != "" {
		t.Fatalf("nothing owed: %q %v", kind, err)
	}
	if w, _ := m.WaitingFor("a"); w {
		t.Fatal("waiting for nothing")
	}
	if wake, _ := m.WouldWake(row); wake {
		t.Fatal("nothing tries to wake it")
	}
	pid, _ := db.AddPending(m.Q, "a", "hello", 1)
	kind, what, err := m.StartOrWakeNeeded("a", row)
	if err != nil || kind != "wake" || !strings.Contains(what, "1 pending message") {
		t.Fatalf("%q %q %v", kind, what, err)
	}
	if w, _ := m.WaitingFor("a"); !w {
		t.Fatal("a pending message is waited on")
	}
	if wake, _ := m.WouldWake(row); !wake {
		t.Fatal("a cause wakes it")
	}
	// A due deadline wakes a parked one with no cause; an open session never wakes.
	db.DeletePending(m.Q, pid)
	row.State, row.WakeAt.Valid, row.WakeAt.Int64 = states.Parked, true, 1
	if wake, _ := m.WouldWake(row); !wake {
		t.Fatal("due while parked")
	}
	open, _ := db.GetAgentState(m.Q, "b")
	if wake, _ := m.WouldWake(open); wake {
		t.Fatal("no row")
	}
	// No terminal and work queued: started, not woken.
	if kind, what, _ := m.StartOrWakeNeeded("c", nil); kind != "" || what != "" {
		t.Fatal("a stranger with nothing waiting")
	}
	db.AddPending(m.Q, "c", "hi", 1)
	if kind, what, _ := m.StartOrWakeNeeded("c", nil); kind != "start" || what != "work waiting" {
		t.Fatalf("%q %q", kind, what)
	}
}

func TestRestartAgentRefusesAWorkingAgentAndRestartsAnIdleOneInPlace(t *testing.T) {
	m, d, tm := newManager(t)
	tm.launchOK = true
	d.waitingMsg, d.tookPrompt = "Restarted.", true
	if ok, said, _ := m.RestartAgent("ghost", SessionStatus{}, RestartOpts{}); ok || said != "it has never been started" {
		t.Fatalf("%v %q", ok, said)
	}
	db.SetTerminal(m.Q, "a", "t1", "/wt")
	db.SetAgentState(m.Q, "a", states.Running, 0, "")
	if ok, said, _ := m.RestartAgent("a", SessionStatus{SessionID: "sess-1"}, RestartOpts{}); ok || !strings.Contains(said, "mid-turn") {
		t.Fatalf("%v %q", ok, said)
	}
	db.SetAgentState(m.Q, "a", states.Handoff, 0, "")
	if ok, said, _ := m.RestartAgent("a", SessionStatus{SessionID: "sess-1"}, RestartOpts{Force: true}); ok || said != "it is mid-handoff" {
		t.Fatalf("%v %q", ok, said)
	}
	db.SetAgentState(m.Q, "a", states.Idle, 0, "")
	db.AddEvent(m.Q, "a", "Stop")
	ok, said, err := m.RestartAgent("a", SessionStatus{SessionID: "sess-1"}, RestartOpts{By: "boss"})
	if err != nil || !ok {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	if len(tm.relaunched) != 1 || !strings.HasSuffix(tm.relaunched[0], "--resume sess-1") || tm.closedPane {
		t.Fatalf("relaunched %v, pane closed %v", tm.relaunched, tm.closedPane)
	}
	if row, _ := db.GetAgentState(m.Q, "a"); isClosed(row) {
		t.Fatalf("%+v", row)
	}
}

func TestAWakeNobodyGaveACauseSaysWhatEndedTheLastSession(t *testing.T) {
	m, _, _ := newManager(t)
	cause := func(agent string, events ...db.KV) string {
		t.Helper()
		db.SetTerminal(m.Q, agent, "", "/wt")
		db.SetClosed(m.Q, agent, "")
		for _, e := range events {
			if _, err := db.AddEvent(m.Q, agent, e.K, db.KV{K: "kind", V: e.V}); err != nil {
				t.Fatal(err)
			}
		}
		row, _ := db.GetAgentState(m.Q, agent)
		return m.wakeCause(agent, row, "")
	}
	for _, c := range []struct {
		agent, want string
		events      []db.KV
	}{
		{"plain", "wake", nil},
		{"fresh", "fresh close", []db.KV{{K: "fresh_close"}}},
		{"folded", "context_full", []db.KV{{K: "fresh_close"}, {K: states.FoldKind}}},
		{"full", "context_full", []db.KV{{K: "park", V: "context"}}},
		{"walled", "wake", []db.KV{{K: "park", V: "usage"}}},
		{"idle", "idle_close", []db.KV{{K: IdleCloseKind}}},
	} {
		if got := cause(c.agent, c.events...); got != c.want {
			t.Errorf("%s: %q, want %q", c.agent, got, c.want)
		}
	}
	row, _ := db.GetAgentState(m.Q, "idle")
	if got := m.wakeCause("idle", row, "acct2"); got != "move" {
		t.Errorf("a move says so: %q", got)
	}
	row.ClosedAt.Int64 += 10 // the idle close was of an earlier session
	if got := m.wakeCause("idle", row, ""); got != "wake" {
		t.Errorf("an event before the close says nothing of it: %q", got)
	}
}
