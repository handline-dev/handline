package tick

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func ip(n int) *int { return &n }

func TestEvaluate(t *testing.T) {
	cases := []struct {
		name         string
		short, ctx   *int
		long         *int
		limit        int
		fired        bool
		kind, reason string
	}{
		{"short wall", ip(100), nil, nil, 200000, true, "usage", "shortest window 100% - at the wall"},
		{"long wall", ip(10), nil, ip(100), 200000, true, "usage_7d", "longest window 100% - at the wall"},
		{"context", ip(10), ip(1234567), nil, 1000000, true, "context", "context 1,234,567 >= 1,000,000"},
		{"unknown is not a wall", nil, nil, nil, 200000, false, "", "shortest window ? below the wall, context ? < 200,000"},
	}
	for _, c := range cases {
		got := Evaluate(c.short, c.ctx, c.long, c.limit)
		if got.Fired != c.fired || got.Kind != c.kind || got.Reason != c.reason {
			t.Errorf("%s: %+v", c.name, got)
		}
	}
}

func TestBurnRate(t *testing.T) {
	if r := BurnRatePerS([]Sample{{0, 10, true}, {60, 20, true}}); r != 0 {
		t.Errorf("span under the minimum measured %v", r)
	}
	if r := BurnRatePerS([]Sample{{0, 10, true}, {200, 20, true}, {100, 15, false}}); r != 0.05 {
		t.Errorf("rate %v", r)
	}
	if r := BurnRatePerS([]Sample{{0, 50, true}, {500, 5, true}}); r != 0 {
		t.Errorf("a reset measured %v", r)
	}
}

func TestStayOrMove(t *testing.T) {
	soon := 600.0
	if move, _ := StayOrMove(false, nil, StayWithinS); move {
		t.Error("an idle agent moved")
	}
	if move, _ := StayOrMove(true, &soon, StayWithinS); move {
		t.Error("moved with a reset in ten minutes")
	}
	if move, _ := StayOrMove(true, nil, StayWithinS); !move {
		t.Error("an unknown reset is further than T")
	}
}

func TestWakeAfter(t *testing.T) {
	if got := WakeAfter("usage", 1000, 9000, 2000); got != 2300 {
		t.Errorf("past reset: %d", got)
	}
	if got := WakeAfter("usage_7d", 1000, 9000, 2000); got != 9000 {
		t.Errorf("long window: %d", got)
	}
}

func TestDevModeRestartsOnlyAfterQuietAndStarts(t *testing.T) {
	exe := filepath.Join(t.TempDir(), "daemon")
	if err := os.WriteFile(exe, []byte("v1"), 0o755); err != nil {
		t.Fatal(err)
	}
	now := time.Unix(1000, 0)
	on := true
	var log []string
	var startsOK = true
	d := &DevMode{Enabled: func() bool { return on }, Exe: exe, Log: func(s string) { log = append(log, s) },
		Now: func() time.Time { return now }, Check: func(string) error {
			if !startsOK {
				return os.ErrInvalid
			}
			return nil
		}}
	d.Baseline()
	if d.ShouldRestart() {
		t.Fatal("restart on an unchanged executable")
	}
	if err := os.WriteFile(exe, []byte("v2-longer"), 0o755); err != nil {
		t.Fatal(err)
	}
	if d.ShouldRestart() {
		t.Fatal("restart before the tree stood quiet")
	}
	now = now.Add(QuietS)
	startsOK = false
	if d.ShouldRestart() || len(log) != 1 {
		t.Fatalf("restarted onto a binary that does not start: %v", log)
	}
	if d.ShouldRestart() || len(log) != 1 {
		t.Fatal("a version already found broken was checked again")
	}
	if err := os.WriteFile(exe, []byte("v3-even-longer"), 0o755); err != nil {
		t.Fatal(err)
	}
	d.ShouldRestart() // new stamp: pending starts over
	now = now.Add(QuietS)
	startsOK = true
	if !d.ShouldRestart() {
		t.Fatal("no restart onto a good, quiet binary")
	}
	on = false
	if d.ShouldRestart() {
		t.Fatal("restart with dev mode off")
	}
}

func TestLoopStopsAndRestarts(t *testing.T) {
	stop := make(chan struct{})
	var log []string
	l := &Loop{Tick: &Tick{}, Log: func(s string) { log = append(log, s) }, Stop: stop, MaxTicks: 0,
		Sleep: func(time.Duration) bool { return false }}
	// A Tick with no ports cannot run; the loop's own paths are what is tested, through a stopped channel.
	close(stop)
	if l.Run(nil) || len(log) != 1 || log[0] != "stopped after 0 tick(s)" {
		t.Fatalf("%v", log)
	}
}
