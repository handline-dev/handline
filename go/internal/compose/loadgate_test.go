package compose

import (
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/sysres"
)

func TestForceStartPassesOnlyWorkTheLoadHolds(t *testing.T) {
	machine := &sysres.Machine{Cores: 4, Load1: 2, RAMTotal: 100, RAMFree: 50}
	p, home := rigWith(t, func(c *Config) { c.Machine = func() *sysres.Machine { return machine } })
	lc := p.Lifecycle(cfgOf(p), LifecycleConfig{Env: &supervisor.Env{DB: p.db, Home: home, Log: func(string, ...any) {}}})
	c := controls{p: p, lc: lc}
	db.SetAgentState(p.db, "alice", states.Running, 0, "") // one mid-turn, so a start may be held
	p.db.Exec("UPDATE agent_state SET account = 'a' WHERE agent = 'alice'")
	db.AddPending(p.db, "bob", "job", 1)
	if ok, said, _ := c.ForceStart("bob"); ok || !strings.Contains(said, "holds nothing") {
		t.Fatalf("under the limits: %v %q", ok, said)
	}
	machine.Load1 = 8 // 2.00 per core
	if lc.Gate.Held("bob") == "" {
		t.Fatal("over the load limit and not held")
	}
	if ok, said, _ := c.ForceStart("carol"); ok || !strings.Contains(said, "holds nothing") {
		t.Fatalf("nothing waits for carol: %v %q", ok, said)
	}
	ok, said, err := c.ForceStart("bob")
	if err != nil || !ok || !strings.Contains(said, "load 2.00 per core, limit 1.50") {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	if lc.Gate.Held("bob") != "" || lc.Gate.Held("carol") == "" {
		t.Fatal("the pass is bob's alone")
	}
	var n int
	p.db.QueryRow("SELECT COUNT(*) FROM events WHERE agent = 'bob' AND kind = 'load_limit_passed'").Scan(&n)
	if n != 1 {
		t.Fatalf("the force start is not on the timeline: %d", n)
	}
}
