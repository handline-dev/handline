package loadgate

import (
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/sysres"
	"github.com/handline/handline/go/internal/usage"
)

func TestLimitsDefaultAndAreSetByConfig(t *testing.T) {
	if got := LimitsOf(usage.Config{}); got != (Limits{Agents: 0, LoadPct: 150, RAMFreePct: 10, Simulator: 1}) {
		t.Fatalf("defaults: %+v", got)
	}
	cfg := usage.Config{AgentCapKey: int64(4), LoadPctKey: 0, RAMFreePctKey: "x", SimulatorKey: 0}
	if got := LimitsOf(cfg); got != (Limits{Agents: 4, LoadPct: 0, RAMFreePct: 10}) {
		t.Fatalf("set: %+v", got)
	}
}

func TestEachLimitHoldsAStartAndNoneHoldsTheFirst(t *testing.T) {
	l := Limits{Agents: 3, LoadPct: 150, RAMFreePct: 10}
	calm := &sysres.Machine{Cores: 8, Load1: 4, RAMTotal: 100, RAMFree: 50}
	busy := &sysres.Machine{Cores: 8, Load1: 16, RAMTotal: 100, RAMFree: 5}
	cases := []struct {
		name   string
		l      Limits
		active int
		m      *sysres.Machine
		held   string
	}{
		{"calm", l, 2, calm, ""},
		{"agents at the cap", l, 3, calm, "Held: 3 of 3 agents are working. It starts by itself when one finishes."},
		{"agent cap off", Limits{LoadPct: 150}, 9, calm, ""},
		{"load over", l, 1, &sysres.Machine{Cores: 8, Load1: 12.8, RAMTotal: 100, RAMFree: 50}, "Held: the Mac is busy (1.6 per core, limit 1.5). It starts by itself when that drops."},
		{"load just over", l, 1, &sysres.Machine{Cores: 8, Load1: 12.08, RAMTotal: 100, RAMFree: 50}, "the Mac is busy (1.51 per core, limit 1.50)"},
		{"load at the limit", l, 1, &sysres.Machine{Cores: 8, Load1: 12, RAMTotal: 100, RAMFree: 50}, ""},
		{"memory short", l, 1, &sysres.Machine{Cores: 8, Load1: 1, RAMTotal: 100, RAMFree: 9}, "Held: the Mac is low on memory (9% free, limit 10%). It starts by itself when memory frees up."},
		{"limits off", Limits{}, 1, busy, ""},
		{"an emulator runs", Limits{Simulator: 1}, 1, &sysres.Machine{Cores: 8, Simulator: "Android emulator"}, "Held: the Android emulator is running. It starts by itself when it closes."},
		{"a simulator runs, hold off", Limits{LoadPct: 150}, 1, &sysres.Machine{Cores: 8, Simulator: "iOS simulator"}, ""},
		{"the cap is said first", Limits{Agents: 1, Simulator: 1}, 1, &sysres.Machine{Simulator: "iOS simulator"}, "1 of 1 agents are working"},
		{"a simulator comes before the load", Limits{LoadPct: 150, Simulator: 1}, 1, &sysres.Machine{Cores: 8, Load1: 16, Simulator: "iOS simulator"}, "the iOS simulator is running"},
		{"no agent mid-turn, a simulator holds nothing", Limits{Simulator: 1}, 0, &sysres.Machine{Simulator: "iOS simulator"}, ""},
		{"no measure yet", l, 1, nil, ""},
		{"nothing mid-turn goes under any load", l, 0, busy, ""},
	}
	for _, c := range cases {
		got := Held(c.l, c.active, c.m)
		if (c.held == "") != (got == "") || !strings.Contains(got, c.held) {
			t.Errorf("%s: held %q, want %q", c.name, got, c.held)
		}
	}
}

func TestAPassLetsOneAgentPastTheLimitsForAWhile(t *testing.T) {
	now := time.Unix(1000, 0)
	g := &Gate{Limits: func() Limits { return Limits{Agents: 1} }, Active: func() int { return 1 }, Now: func() time.Time { return now }}
	if g.Held("amy") == "" {
		t.Fatal("at the cap and not held")
	}
	g.Pass("amy")
	if g.Held("amy") != "" || g.Held("bob") == "" {
		t.Fatalf("pass: amy %q, bob %q", g.Held("amy"), g.Held("bob"))
	}
	now = now.Add(PassFor)
	if g.Held("amy") == "" {
		t.Fatal("the pass outlived PassFor")
	}
	var none *Gate
	if none.Held("amy") != "" {
		t.Fatal("a nil gate held")
	}
}

func TestWorkHeldPastTheCeilingGoesOnceAndSaysWhy(t *testing.T) {
	now := time.Unix(1000, 0)
	g := &Gate{Limits: func() Limits { return Limits{Agents: 1} }, Active: func() int { return 1 }, Now: func() time.Time { return now }}
	var said []string
	ceiling := func(why string) { said = append(said, why) }
	if g.ForWork("amy", ceiling) == "" {
		t.Fatal("at the cap and not held")
	}
	now = now.Add(Ceiling - time.Second)
	if g.ForWork("amy", ceiling) == "" || len(said) != 0 {
		t.Fatalf("let go before the ceiling: %v", said)
	}
	now = now.Add(time.Second)
	if g.ForWork("amy", ceiling) != "" || len(said) != 1 || !IsHeld(said[0]) {
		t.Fatalf("at the ceiling: still held, or not said once: %v", said)
	}
	if g.ForWork("amy", ceiling) != "" || len(said) != 1 {
		t.Fatalf("the pass is said once: %v", said)
	}
	if g.Held("bob") == "" {
		t.Fatal("amy's ceiling let bob go too")
	}
	now = now.Add(PassFor)
	if g.ForWork("amy", ceiling) == "" {
		t.Fatal("a ceiling pass outlived PassFor; the next hold starts its own clock")
	}
}

func TestWorkThatWentAwayLeavesNoClock(t *testing.T) {
	now := time.Unix(1000, 0)
	g := &Gate{Limits: func() Limits { return Limits{Agents: 1} }, Active: func() int { return 1 }, Now: func() time.Time { return now }}
	g.ForWork("amy", nil)
	g.Forget("amy") // amy's work went away while the load stayed high; new work comes an hour later
	now = now.Add(time.Hour)
	if g.ForWork("amy", func(string) { t.Fatal("old clock let new work past") }) == "" {
		t.Fatal("new work went past the hold at once")
	}
	var none *Gate
	none.Forget("amy")
}

func TestTheCeilingIsSaidOutsideTheLock(t *testing.T) {
	now := time.Unix(1000, 0)
	g := &Gate{Limits: func() Limits { return Limits{Agents: 1} }, Active: func() int { return 1 }, Now: func() time.Time { return now }}
	g.ForWork("amy", nil)
	now = now.Add(Ceiling)
	g.ForWork("amy", func(string) { g.Pass("bob") }) // would deadlock if called with g.mu held
	if g.Held("bob") != "" {
		t.Fatal("onCeiling did not run")
	}
	var none *Gate
	if none.ForWork("amy", nil) != "" {
		t.Fatal("a nil gate held work")
	}
}

func TestTheCauseOfAHoldIsItsFirstClause(t *testing.T) {
	l := Limits{Agents: 3, LoadPct: 150, RAMFreePct: 10}
	for m, want := range map[*sysres.Machine]string{
		{Cores: 8, Load1: 1, RAMTotal: 100, RAMFree: 50}:  "3 of 3 agents are working",
		{Cores: 8, Load1: 16, RAMTotal: 100, RAMFree: 50}: "the Mac is busy",
		{Cores: 8, Load1: 1, RAMTotal: 100, RAMFree: 5}:   "the Mac is low on memory",
	} {
		active := 1
		if want == "3 of 3 agents are working" {
			active = 3
		}
		if got := Cause(Held(l, active, m)); got != want {
			t.Errorf("cause %q, want %q", got, want)
		}
	}
}
