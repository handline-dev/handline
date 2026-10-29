package lifecycle

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/staymove"
)

// moveLaunch is the room test: the account it names, or why none has room.
type moveLaunch struct {
	fakeLaunch
	dest, why string
	excluded  map[string]string
	roomOn    string
}

func (l *moveLaunch) RoomElsewhere(_ db.Q, _ string, _ *db.AgentState, ex map[string]string) (string, string) {
	l.excluded = ex
	return l.dest, l.why
}
func (l *moveLaunch) RoomOn(db.Q, string, *db.AgentState, string) string { return l.roomOn }

type moveRig struct {
	m  *Manager
	l  *moveLaunch
	v  *Mover
	bg string
	h  map[string]bool
}

// newMoveRigOn is the mover over a manager that is already set up.
func newMoveRigOn(m *Manager, l *moveLaunch) *moveRig {
	rig := &moveRig{m: m, l: l}
	rig.v = &Mover{M: m, E: MoveEnv{
		Reading:        func(db.Q, string) (string, string, bool) { return "acct", "recorded", true },
		Windows:        func(string) Windows { return Windows{} },
		ColdStartPct:   func(db.Q) *float64 { return nil },
		AtLimit:        func(db.Q, string) (int64, bool) { return 0, false },
		NoteHold:       func(db.Q, string, int64) bool { return false },
		BackgroundWork: func(db.Q, string) string { return rig.bg },
		ContextUse:     func(db.Q, string) (int, int) { return 190000, 200000 },
		MeasureForget:  func(db.Q, string) {},
		StayOrMove:     func(staymove.Situation) (bool, string) { return true, "" },
		WakeAfter:      func(_ string, short, _, now int64) int64 { return now + 1000 },
	}}
	return rig
}

func (g *moveRig) row(t *testing.T) *db.AgentState {
	t.Helper()
	row, err := db.GetAgentState(g.m.Q, "a")
	if err != nil || row == nil {
		t.Fatalf("%v", err)
	}
	return row
}

func TestADeferredHandoffThreeTicksRunningLeavesTheCountersAtZero(t *testing.T) {
	g := newTermMoveRig(t)
	for _, c := range []struct {
		name  string
		setup func()
	}{
		{"mid-turn", func() { g.run.status = "busy" }},
		{"note hold", func() { g.run.status = "idle"; g.v.E.NoteHold = func(db.Q, string, int64) bool { return true } }},
	} {
		c.setup()
		for i := 0; i < 3; i++ {
			if ok, err := g.v.PerformHandoff("a", HandoffTrigger{"usage", "wall"}, ""); !ok || err != nil {
				t.Fatalf("%s round %d: %v %v", c.name, i, ok, err)
			}
		}
		row := g.row(t)
		if row.ThrottleCount != 0 || row.FailedAttempts != 0 || row.State == states.Handoff || len(g.pane.handles) != 0 {
			t.Fatalf("%s: %+v closed %v", c.name, row, g.pane.handles)
		}
		if h, _ := db.InFlightFor(g.m.Q, "a"); h != nil {
			t.Fatalf("%s: a handoff row was started", c.name)
		}
		if _, _, waits, _ := db.NoteWait(g.m.Q, "a"); waits != (c.name == "note hold") {
			t.Fatalf("%s: note wait kept %v", c.name, waits)
		}
	}
}

func TestAWakeWaitsOnItsWallOnlyForAResetWithinThePromptCachesLife(t *testing.T) {
	g := newTermMoveRig(t)
	g.v.E.StayOrMove = staymove.Decide
	dear := 40.0 // a cold start measured dearer than the cache lives: 2h of a 5h window
	g.v.E.ColdStartPct = func(db.Q) *float64 { return &dear }
	// Nothing is queued for it: only the wake asks for it.
	at := func(resetIn int64) Windows { return Windows{ShortReset: g.m.now() + resetIn, ShortS: 5 * 3600} }
	g.v.E.Windows = func(string) Windows { return at(30 * 60) }
	if dest, why, _ := g.v.MovePlan("a", "usage", true); dest != "" || !strings.HasSuffix(why, "cheaper to wait") {
		t.Fatalf("a reset within the cache's life is waited for: %q %q", dest, why)
	}
	g.v.E.Windows = func(string) Windows { return at(90 * 60) }
	if dest, why, _ := g.v.MovePlan("a", "usage", true); dest != "beta" {
		t.Fatalf("past it, the wake moves at once, though the cold start costs more: %q %q", dest, why)
	}
	if dest, why, _ := g.v.MovePlan("a", "usage", false); dest != "" || !strings.HasPrefix(why, "no work is waiting") {
		t.Fatalf("a live agent with nothing waiting stays: %q %q", dest, why)
	}
	// Its last turn ended 50 minutes ago: by a reset 30 minutes off the cache is cold, so waiting saves nothing.
	db.AddEventAt(g.m.Q, g.m.now()-50*60, "a", "StopFailure")
	g.v.E.Windows = func(string) Windows { return at(30 * 60) }
	if dest, why, _ := g.v.MovePlan("a", "usage", true); dest != "beta" {
		t.Fatalf("a session whose cache is cold by the reset waited: %q %q", dest, why)
	}
}

func TestAMoveLeavesOutTheAccountItIsAtAndTheOneItJustLeftUntilItResets(t *testing.T) {
	g := newTermMoveRig(t)
	db.AddEvent(g.m.Q, "a", "moved_account", db.KV{K: "from", V: "gamma"}, db.KV{K: "to", V: "acct"})
	g.v.E.Windows = func(acct string) Windows {
		if acct == "gamma" {
			return Windows{ShortReset: g.m.now() + 600}
		}
		return Windows{}
	}
	if dest, _, err := g.v.MovePlan("a", "usage", false); err != nil || dest != "beta" {
		t.Fatalf("%q %v", dest, err)
	}
	if _, here := g.l.excluded["acct"]; !here {
		t.Fatalf("its own account was a candidate: %v", g.l.excluded)
	}
	if _, left := g.l.excluded["gamma"]; !left {
		t.Fatalf("the account it just left was a candidate: %v", g.l.excluded)
	}
	// once that account has reset it is a place to go again
	g.v.E.Windows = func(string) Windows { return Windows{} }
	g.v.MovePlan("a", "usage", false)
	if _, left := g.l.excluded["gamma"]; left {
		t.Fatalf("still excluded after its reset: %v", g.l.excluded)
	}
}

func TestAForcedMoveTheRoomTestRefusesLeavesTheSessionAndTheRecordAlone(t *testing.T) {
	g := newTermMoveRig(t)
	db.SetHandoff(g.m.Q, "a", "earlier record")
	g.l.roomOn = "has no free slot"
	if ok, _ := g.v.PerformHandoff("a", HandoffTrigger{"forced", "by hand"}, "beta"); ok {
		t.Fatal("reported as done")
	}
	row := g.row(t)
	if len(g.pane.handles) != 0 || g.run.status == "" || row.State != states.Idle {
		t.Fatalf("closed %v %+v", g.pane.handles, row)
	}
	if h, _ := db.GetHandoff(g.m.Q, "a"); h != "earlier record" {
		t.Fatalf("record %q", h)
	}
}

func TestAnOwedClearIsForcedThroughWhenItsTimeIsUpAndTheRecordSaysWhatWasCutOff(t *testing.T) {
	g := newTermMoveRig(t)
	g.bg = "npm test"
	g.v.PerformHandoff("a", HandoffTrigger{"context", "full"}, "")
	db.SetCursor(g.m.Q, "clear_owed:a", g.m.now()-ClearWaitS-1)
	if owed, _ := g.v.SettleOwedClear("a"); !owed || len(g.pane.handles) != 1 {
		t.Fatalf("owed %v closed %v", owed, g.pane.handles)
	}
	if h, _ := db.GetHandoff(g.m.Q, "a"); !strings.Contains(h, "Cut off by the clear") || !strings.Contains(h, "npm test") {
		t.Fatalf("record %q", h)
	}
}

func (g *moveRig) account(t *testing.T, name string, loggedIn bool) {
	t.Helper()
	dir := filepath.Join(g.m.Home, "accounts", name, ".claude")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if loggedIn {
		os.WriteFile(filepath.Join(dir, ".credentials.json"), []byte("{}"), 0o600)
	}
}

func TestAForcedMoveIsRefusedForAnAccountThatCannotTakeItAndNothingIsEnded(t *testing.T) {
	g := newTermMoveRig(t)
	g.account(t, "beta", true)
	g.account(t, "out", false)
	g.account(t, "acct", true)
	for _, c := range []struct{ dest, want string }{
		{"nowhere", "account 'nowhere' does not exist or is not logged in"},
		{"out", "account 'out' does not exist or is not logged in"},
		{"acct", "a is already on acct"},
	} {
		if ok, said, err := g.v.ForceMove("a", c.dest, "human"); ok || err != nil || said != c.want {
			t.Errorf("%s: %v %q %v", c.dest, ok, said, err)
		}
	}
	g.l.roomOn = "has no free slot"
	if ok, said, _ := g.v.ForceMove("a", "beta", "human"); ok || said != "beta has no free slot - not moving a there" {
		t.Fatalf("%v %q", ok, said)
	}
	db.SetAgentState(g.m.Q, "a", states.Stalled, 0, "x")
	g.l.roomOn = ""
	if ok, said, _ := g.v.ForceMove("a", "beta", "human"); ok || said != "a is STALLED" {
		t.Fatalf("%v %q", ok, said)
	}
	if len(g.pane.handles) != 0 || len(g.sp.startedIn) != 0 {
		t.Fatalf("a refusal ended or started a session: %v %v", g.pane.handles, g.sp.startedIn)
	}
}

func TestAForcedMoveEndsTheSessionStartsItOnTheOtherAccountAndRecordsWhoDidIt(t *testing.T) {
	g := newTermMoveRig(t)
	g.account(t, "beta", true)
	ok, said, err := g.v.ForceMove("a", "beta", "human (screen)")
	if !ok || err != nil || said != "a moved acct -> beta (forced by human (screen))" {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	if len(g.pane.handles) != 1 || len(g.sp.startedIn) != 1 || g.row(t).Account.String != "beta" {
		t.Fatalf("closed %v opened %+v %+v", g.pane.handles, g.sp.startedIn, g.row(t))
	}
	var payload string
	if err := g.m.Q.QueryRow("SELECT payload FROM events WHERE agent = 'a' AND kind = 'forced_move'").Scan(&payload); err != nil || !strings.Contains(payload, `"by": "human (screen)"`) && !strings.Contains(payload, `"by":"human (screen)"`) {
		t.Fatalf("%q %v", payload, err)
	}
}

func TestAForcedMoveOfAClosedAgentIsOwedToItsNextWake(t *testing.T) {
	g := newTermMoveRig(t)
	g.account(t, "beta", true)
	db.SetClosed(g.m.Q, "a", "sess-1")
	g.run.status = ""
	ok, said, err := g.v.ForceMove("a", "beta", "human")
	if !ok || err != nil || said != "a is closed: its next wake starts it on beta (moved by human)" {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	if _, found, _ := db.LastEventID(g.m.Q, "a", MoveOwedKind); !found || len(g.sp.startedIn) != 0 {
		t.Fatalf("owed %v opened %v", found, g.sp.startedIn)
	}
}

// A trigger deferred behind a running turn holds messages from that moment; the row opening ends the hold's mark.
func TestADeferredTriggerHoldsMessagesFromTheMomentItIsDeferredUntilItsRowOpens(t *testing.T) {
	g := newTermMoveRig(t)
	g.run.status = "busy"
	if ok, err := g.v.PerformHandoff("a", HandoffTrigger{"usage", "wall"}, ""); !ok || err != nil {
		t.Fatal(ok, err)
	}
	if held, _ := db.RestartHeld(g.m.Q, "a", g.m.now()); !held {
		t.Fatal("not held while the trigger is deferred")
	}
	if held, _ := db.RestartHeld(g.m.Q, "a", g.m.now()+db.NoteWaitStaleS+1); held {
		t.Fatal("a deferral held past the ceiling")
	}
	g.run.status = "idle"
	if ok, err := g.v.PerformHandoff("a", HandoffTrigger{"usage", "wall"}, ""); !ok || err != nil {
		t.Fatal(ok, err)
	}
	if v, _ := db.Cursor(g.m.Q, "handoff_deferred:a"); v != 0 {
		t.Fatal("the deferred mark is kept after the row opened")
	}
}
