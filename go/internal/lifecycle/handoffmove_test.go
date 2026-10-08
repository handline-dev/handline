package lifecycle

import (
	"errors"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
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
	r  *fakeRunner
	l  *moveLaunch
	v  *Mover
	bg string
	h  map[string]bool
}

func newMoveRig(t *testing.T) *moveRig {
	t.Helper()
	m, r, d, _ := structuredManager(t)
	d.waitingMsg = "work"
	l := &moveLaunch{dest: "beta"}
	m.Launch = l
	db.SetTerminal(m.Q, "a", "", "/wt")
	db.SetResumeSession(m.Q, "a", "sess-1")
	r.alive["a"] = true
	rig := &moveRig{m: m, r: r, l: l}
	rig.v = &Mover{M: m, E: MoveEnv{
		Reading:        func(db.Q, string) (string, string, bool) { return "acct", "recorded", true },
		Windows:        func(string) Windows { return Windows{} },
		ColdStartPct:   func(db.Q) *float64 { return nil },
		AtLimit:        func(db.Q, string) (int64, bool) { return 0, false },
		NoteHold:       func(db.Q, string, int64) bool { return false },
		BackgroundWork: func(db.Q, string) string { return rig.bg },
		ContextUse:     func(db.Q, string) (int, int) { return 190000, 200000 },
		RunningAccount: func(string) string { return "" },
		MeasureForget:  func(db.Q, string) {},
		StayWithin:     func(*float64, float64) float64 { return 0 },
		StayOrMove:     func(bool, *float64, float64) (bool, string) { return true, "" },
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

func TestAnAgentAtTheWallIsClosedMovedAndWokenOnTheOtherAccountOnce(t *testing.T) {
	g := newMoveRig(t)
	if ok, err := g.v.PerformHandoff("a", HandoffTrigger{"usage", "5h wall"}, ""); !ok || err != nil {
		t.Fatalf("%v %v", ok, err)
	}
	if len(g.r.closed) != 1 || len(g.r.opened) != 1 || g.r.opened[0].Account != "beta" || g.r.opened[0].Resume {
		t.Fatalf("closed %v opened %+v", g.r.closed, g.r.opened)
	}
	if len(g.r.sent) != 1 || !strings.Contains(g.r.sent[0], "started on beta") {
		t.Fatalf("sent %v", g.r.sent)
	}
	row := g.row(t)
	if row.Account.String != "beta" || isClosed(row) || row.State != states.Queued {
		t.Fatalf("%+v", row)
	}
	if _, ok, _ := db.LastEventID(g.m.Q, "a", "moved_account"); !ok {
		t.Fatal("no moved_account event")
	}
	// the trigger fires again at once: nothing is closed or sent twice
	if ok, _ := g.v.PerformHandoff("a", HandoffTrigger{"usage", "5h wall"}, ""); !ok || len(g.r.closed) != 1 || len(g.r.sent) != 1 {
		t.Fatalf("closed %v sent %v", g.r.closed, g.r.sent)
	}
}

func TestADeferredHandoffThreeTicksRunningLeavesTheCountersAtZero(t *testing.T) {
	g := newMoveRig(t)
	for _, c := range []struct {
		name  string
		setup func()
	}{
		{"mid-turn", func() { g.r.busy["a"] = true }},
		{"note hold", func() { g.r.busy["a"] = false; g.v.E.NoteHold = func(db.Q, string, int64) bool { return true } }},
	} {
		c.setup()
		for i := 0; i < 3; i++ {
			if ok, err := g.v.PerformHandoff("a", HandoffTrigger{"usage", "wall"}, ""); !ok || err != nil {
				t.Fatalf("%s round %d: %v %v", c.name, i, ok, err)
			}
		}
		row := g.row(t)
		if row.ThrottleCount != 0 || row.FailedAttempts != 0 || row.State == states.Handoff || len(g.r.closed) != 0 {
			t.Fatalf("%s: %+v closed %v", c.name, row, g.r.closed)
		}
		if h, _ := db.InFlightFor(g.m.Q, "a"); h != nil {
			t.Fatalf("%s: a handoff row was started", c.name)
		}
	}
}

func TestAContextHandoffClosesFreshAndParksWithAnImmediateWake(t *testing.T) {
	g := newMoveRig(t)
	if ok, _ := g.v.PerformHandoff("a", HandoffTrigger{"context", "190k of 200k"}, ""); !ok {
		t.Fatal("degraded")
	}
	row := g.row(t)
	if len(g.r.closed) != 1 || !isClosed(row) || row.ResumeSession.String != "" || row.State != states.Parked {
		t.Fatalf("closed %v %+v", g.r.closed, row)
	}
	if row.WakeAt.Int64 > g.m.now() {
		t.Fatalf("wake in the future: %+v", row)
	}
	if len(g.r.opened) != 0 || len(g.r.sent) != 0 {
		t.Fatalf("a context handoff starts nothing itself: %v %v", g.r.opened, g.r.sent)
	}
	if ok, said, _ := g.m.WakeAgent("a", WakeOpts{Force: true}); !ok || len(g.r.opened) != 1 || g.r.opened[0].Resume {
		t.Fatalf("%v %q", g.r.opened, said)
	}
}

func TestACloseThatIsRefusedLeavesTheAgentOnItsAccountAndLooksAgainSoon(t *testing.T) {
	g := newMoveRig(t)
	g.r.closeErr = errors.New("a child is still running")
	if ok, _ := g.v.PerformHandoff("a", HandoffTrigger{"usage", "wall"}, ""); !ok {
		t.Fatal("degraded")
	}
	row := g.row(t)
	if !g.r.alive["a"] || len(g.r.opened) != 0 || row.State != states.Parked || !strings.HasPrefix(row.Detail.String, states.MovingMark) {
		t.Fatalf("%+v", row)
	}
}

func TestAMoveLeavesOutTheAccountItIsAtAndTheOneItJustLeftUntilItResets(t *testing.T) {
	g := newMoveRig(t)
	db.AddEvent(g.m.Q, "a", "moved_account", db.KV{K: "from", V: "gamma"}, db.KV{K: "to", V: "acct"})
	g.v.E.Windows = func(acct string) Windows {
		if acct == "gamma" {
			return Windows{ShortReset: g.m.now() + 600}
		}
		return Windows{}
	}
	if dest, _, err := g.v.MovePlan("a", "usage"); err != nil || dest != "beta" {
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
	g.v.MovePlan("a", "usage")
	if _, left := g.l.excluded["gamma"]; left {
		t.Fatalf("still excluded after its reset: %v", g.l.excluded)
	}
}

func TestAForcedMoveTheRoomTestRefusesLeavesTheSessionAndTheRecordAlone(t *testing.T) {
	g := newMoveRig(t)
	db.SetHandoff(g.m.Q, "a", "earlier record")
	g.l.roomOn = "has no free slot"
	if ok, _ := g.v.PerformHandoff("a", HandoffTrigger{"forced", "by hand"}, "beta"); ok {
		t.Fatal("reported as done")
	}
	row := g.row(t)
	if len(g.r.closed) != 0 || !g.r.alive["a"] || row.State != states.Idle {
		t.Fatalf("closed %v %+v", g.r.closed, row)
	}
	if h, _ := db.GetHandoff(g.m.Q, "a"); h != "earlier record" {
		t.Fatalf("record %q", h)
	}
}

func TestAClearThatBackgroundWorkHoldsOffIsOwedToldOnceAndSettledWhenTheWorkEnds(t *testing.T) {
	g := newMoveRig(t)
	g.bg = "npm test"
	if ok, _ := g.v.PerformHandoff("a", HandoffTrigger{"context", "full"}, ""); !ok {
		t.Fatal("degraded")
	}
	if len(g.r.sent) != 1 || !strings.Contains(g.r.sent[0], "will be cleared in 5 minutes") || len(g.r.closed) != 0 {
		t.Fatalf("sent %v closed %v", g.r.sent, g.r.closed)
	}
	if row := g.row(t); row.State != states.Idle || !strings.HasPrefix(row.Detail.String, "clear owed") {
		t.Fatalf("%+v", row)
	}
	if owed, _ := g.v.SettleOwedClear("a"); !owed || len(g.r.closed) != 0 || len(g.r.sent) != 1 {
		t.Fatalf("owed %v closed %v sent %v", owed, g.r.closed, g.r.sent)
	}
	g.bg = ""
	if owed, _ := g.v.SettleOwedClear("a"); !owed || len(g.r.closed) != 1 {
		t.Fatalf("owed %v closed %v", owed, g.r.closed)
	}
	if row := g.row(t); row.State != states.Parked || !isClosed(row) {
		t.Fatalf("%+v", row)
	}
	if owed, _ := g.v.SettleOwedClear("a"); owed {
		t.Fatal("still owed after the close")
	}
}

func TestAnOwedClearIsForcedThroughWhenItsTimeIsUpAndTheRecordSaysWhatWasCutOff(t *testing.T) {
	g := newMoveRig(t)
	g.bg = "npm test"
	g.v.PerformHandoff("a", HandoffTrigger{"context", "full"}, "")
	db.SetCursor(g.m.Q, "clear_owed:a", g.m.now()-ClearWaitS-1)
	if owed, _ := g.v.SettleOwedClear("a"); !owed || len(g.r.closed) != 1 {
		t.Fatalf("owed %v closed %v", owed, g.r.closed)
	}
	if h, _ := db.GetHandoff(g.m.Q, "a"); !strings.Contains(h, "Cut off by the clear") || !strings.Contains(h, "npm test") {
		t.Fatalf("record %q", h)
	}
}
