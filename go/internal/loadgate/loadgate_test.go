package loadgate

import (
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/sysres"
	"github.com/handline/handline/go/internal/usage"
)

func TestLimitsDefaultAndAreSetByConfig(t *testing.T) {
	if got := LimitsOf(usage.Config{}); got != (Limits{Agents: 0, LoadPct: 150, RAMFreePct: 10}) {
		t.Fatalf("defaults: %+v", got)
	}
	cfg := usage.Config{AgentCapKey: int64(4), LoadPctKey: 0, RAMFreePctKey: "x"}
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
		{"agents at the cap", l, 3, calm, "3 of 3 agents mid-turn"},
		{"agent cap off", Limits{LoadPct: 150}, 9, calm, ""},
		{"load over", l, 1, &sysres.Machine{Cores: 8, Load1: 12.8, RAMTotal: 100, RAMFree: 50}, "load 1.60 per core, limit 1.50"},
		{"load at the limit", l, 1, &sysres.Machine{Cores: 8, Load1: 12, RAMTotal: 100, RAMFree: 50}, ""},
		{"memory short", l, 1, &sysres.Machine{Cores: 8, Load1: 1, RAMTotal: 100, RAMFree: 9}, "9% of memory free, limit 10%"},
		{"limits off", Limits{}, 1, busy, ""},
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
	for i := 0; i < 9; i++ { // asked each minute, as a tick asks about waiting work
		now = now.Add(time.Minute)
		g.ForWork("amy", ceiling)
	}
	now = now.Add(time.Minute - time.Second)
	if g.ForWork("amy", ceiling) == "" || len(said) != 0 {
		t.Fatalf("let go before the ceiling: %v", said)
	}
	now = now.Add(time.Second)
	if g.ForWork("amy", ceiling) != "" || len(said) != 1 || !strings.Contains(said[0], "held: machine load") {
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

func TestAClockNobodyAsksAboutIsForgotten(t *testing.T) {
	now := time.Unix(1000, 0)
	g := &Gate{Limits: func() Limits { return Limits{Agents: 1} }, Active: func() int { return 1 }, Now: func() time.Time { return now }}
	g.ForWork("amy", nil)
	now = now.Add(time.Hour) // amy's work went away while the load stayed high; new work comes an hour later
	if g.ForWork("amy", func(string) { t.Fatal("old clock let new work past") }) == "" {
		t.Fatal("new work went past the hold at once")
	}
}

func TestTheCeilingIsSaidOutsideTheLock(t *testing.T) {
	now := time.Unix(1000, 0)
	g := &Gate{Limits: func() Limits { return Limits{Agents: 1} }, Active: func() int { return 1 }, Now: func() time.Time { return now }}
	g.ForWork("amy", nil)
	for i := 0; i < 10; i++ {
		now = now.Add(time.Minute)
		g.ForWork("amy", func(string) { g.Pass("bob") }) // would deadlock if called with g.mu held
	}
	if g.Held("bob") != "" {
		t.Fatal("onCeiling did not run")
	}
	var none *Gate
	if none.ForWork("amy", nil) != "" {
		t.Fatal("a nil gate held work")
	}
}
