package tick

import (
	"errors"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/store"
)

func openDB(t *testing.T) db.Q {
	t.Helper()
	d, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	return d
}

func TestCyclesAndRing(t *testing.T) {
	edges := map[int64][]int64{1: {2}, 2: {3}, 3: {1}, 4: {1}, 5: {5}, 6: {}}
	got := cycles(edges)
	want := [][]int64{{1, 2, 3}, {5}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("cycles %v, want %v", got, want)
	}
	if r := ring(edges, []int64{1, 2, 3}); !reflect.DeepEqual(r, []int64{1, 2, 3}) {
		t.Errorf("ring %v", r)
	}
	if got := cycles(map[int64][]int64{1: {2}, 2: {}}); len(got) != 0 {
		t.Errorf("a chain is no cycle: %v", got)
	}
}

func TestGuardCatchesEveryPanic(t *testing.T) {
	boom := errors.New("boom")
	for name, f := range map[string]func(){
		"check":  func() { check(boom) },
		"error":  func() { panic(boom) },
		"other":  func() { panic("odd") },
		"nilmap": func() { var m map[string]int; m["a"] = 1 },
	} {
		if err := guard(f); err == nil {
			t.Errorf("%s: panic escaped as success", name)
		}
	}
	if err := guard(func() {}); err != nil {
		t.Error(err)
	}
}

type fakeHost struct {
	Host
	agents []string
}

func (h fakeHost) Agents() []string   { return h.agents }
func (h fakeHost) Accounts() []string { return nil }

type fakeReconciler struct {
	stalled []string
	kinds   []string
}

func (fakeReconciler) Sweep(db.Q, *[]string)        {}
func (fakeReconciler) ExpireHandoffs(db.Q) []string { return nil }
func (r *fakeReconciler) StallAgent(_ db.Q, agent string, _ *[]string, err error, kind string) {
	r.stalled = append(r.stalled, agent+": "+err.Error())
	r.kinds = append(r.kinds, kind)
}

type fakeDelivery struct{ Delivery }

func (fakeDelivery) EnforceEdges(db.Q, string) error { return nil }

type fakeHandoff struct{ Handoff }

// SettleOwedClear panics for "bad": a port that fails by panicking, which the per-agent guard must turn into that agent's stall.
func (fakeHandoff) SettleOwedClear(_ db.Q, agent string) bool {
	if agent == "bad" {
		panic(errors.New("config unreadable"))
	}
	return true // settled: the turn ends here for everyone else
}

type fakeObserve struct{ Observe }

func (fakeObserve) ActiveByAccount(db.Q) map[string][]string { return nil }
func (fakeObserve) FirstTurnAgents(db.Q) map[string]bool     { return nil }
func (fakeObserve) PruneUsageSamples(db.Q) error             { return nil }
func (fakeObserve) PruneTokens(db.Q) error                   { return nil }
func (fakeObserve) RefreshLongRatios(db.Q) error             { return nil }

func newTick(t *testing.T, agents ...string) (*Tick, *fakeReconciler, *[]string) {
	q := openDB(t)
	rec := &fakeReconciler{}
	var logs []string
	tk := &Tick{Q: q, Log: func(s string) { logs = append(logs, s) }, Now: func() int64 { return 1000 },
		Host: fakeHost{agents: agents}, Reconcile: rec, Delivery: fakeDelivery{}, Handoff: fakeHandoff{}, Observe: fakeObserve{},
		Sweeps: Sweeps{CollectSnapshot: func(db.Q) error { return nil }, PollUsage: func(db.Q) error { return nil },
			Today: func() string { return "d" }, BlockedSweep: func(db.Q, int64) {}}}
	return tk, rec, &logs
}

func TestRunStallsOnlyTheAgentWhosePortFailed(t *testing.T) {
	tk, rec, _ := newTick(t, "bad", "good")
	for _, a := range []string{"bad", "good"} {
		if err := db.SetAgentState(tk.Q, a, states.Idle, 0, ""); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := tk.Run(nil); err != nil {
		t.Fatal(err)
	}
	if len(rec.stalled) != 1 || !strings.HasPrefix(rec.stalled[0], "bad: config unreadable") || rec.kinds[0] != "error" {
		t.Fatalf("stalled %v kinds %v", rec.stalled, rec.kinds)
	}
}

func TestSuperviseLeavesStalledAndUnevaluatedAlone(t *testing.T) {
	tk, rec, _ := newTick(t, "s", "x")
	for a, st := range map[string]string{"s": states.Stalled} {
		if err := db.SetAgentState(tk.Q, a, st, 0, ""); err != nil {
			t.Fatal(err)
		}
	}
	var actions []string
	// Every other port is nil: touching one would panic. Stalled is only a human's to clear, so nothing is asked.
	tk.Supervise("s", 1000, map[string][]Sample{}, &actions)
	if len(actions) != 0 || len(rec.stalled) != 0 {
		t.Fatalf("%v %v", actions, rec.stalled)
	}
}

func TestQueuedOrIdle(t *testing.T) {
	q := openDB(t)
	if got, err := QueuedOrIdle(q, "a", "woke"); err != nil || got != states.Idle {
		t.Fatalf("%q %v", got, err)
	}
	row, _ := db.GetAgentState(q, "a")
	if row.State != states.Idle || !strings.Contains(row.Detail.String, "no requests waiting") {
		t.Errorf("%+v", row)
	}
}

func TestStuckWaitsEmpty(t *testing.T) {
	q := openDB(t)
	got, err := StuckWaits(q)
	if err != nil || len(got) != 0 {
		t.Fatalf("%v %v", got, err)
	}
}

func TestRenewalsInStretch(t *testing.T) {
	cases := []struct {
		kinds []string
		want  int
	}{
		{nil, 0},
		{[]string{"cache_renewal", "cache_renewal", "SessionStart", "cache_renewal"}, 2},
		{[]string{"UserPromptSubmit", "cache_renewal", "UserPromptSubmit"}, 1},              // a prompt right after a renewal is its own turn
		{[]string{"cache_renewal", "UserPromptSubmit", "SessionStart", "cache_renewal"}, 1}, // a person's message ends the stretch
	}
	for _, c := range cases {
		if got := RenewalsInStretch(c.kinds); got != c.want {
			t.Errorf("%v: %d, want %d", c.kinds, got, c.want)
		}
	}
}

type fakeSettings struct{ parts []Part }

func (f fakeSettings) FingerprintParts(string) ([]Part, error) { return f.parts, nil }
func (fakeSettings) CurrentAccount(db.Q, string) string        { return "" }

func TestChangedSettings(t *testing.T) {
	a := &ApplySettings{Ports: fakeSettings{[]Part{{"model", "opus"}, {"effort", float64(3)}, {"new", "x"}}}}
	row := &db.AgentState{}
	if got, _ := a.Changed("a", row); got != nil {
		t.Errorf("a launch that recorded no parts is not restarted: %v", got)
	}
	row.LaunchParts.String = `{"model": "sonnet", "effort": 3}`
	row.LaunchParts.Valid = true
	got, err := a.Changed("a", row)
	if err != nil || !reflect.DeepEqual(got, []string{"model"}) {
		t.Errorf("%v %v: only a setting the launch recorded and that differs counts", got, err)
	}
}

func TestDevModeCheckFailsOnRejection(t *testing.T) {
	d := &DevMode{Exe: "/bin/sleep"}
	// The default check runs `Exe --version`; sleep rejects it at once, which is a failed start.
	if err := d.check(); err == nil {
		t.Error("a binary that rejects --version passed the start check")
	}
}
