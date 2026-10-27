package tick

import (
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
)

func TestASlowTickNamesItsLongestStagesAndAFastOneSaysNothing(t *testing.T) {
	var logged []string
	tk := &Tick{Log: func(s string) { logged = append(logged, s) }}
	tk.timed("supervise", func() { time.Sleep(3 * time.Millisecond) })
	tk.timed("supervise", func() {}) // one per agent: summed under the one name
	if len(tk.spans) != 1 || tk.spans[0].took < 3*time.Millisecond {
		t.Fatalf("spans %+v", tk.spans)
	}
	tk.spans = []span{{"agent-view", 150 * time.Millisecond}, {"observe", 900 * time.Millisecond}, {"reconcile", 20 * time.Millisecond}}
	tk.logSlow(500 * time.Millisecond)
	if len(logged) != 0 || len(tk.spans) != 0 {
		t.Fatalf("a quick tick logs nothing and clears its stages: %v %v", logged, tk.spans)
	}
	tk.spans = []span{{"agent-view", 150 * time.Millisecond}, {"observe", 900 * time.Millisecond}, {"reconcile", 20 * time.Millisecond}}
	tk.logSlow(1200 * time.Millisecond)
	if len(logged) != 1 || !strings.Contains(logged[0], "slow tick: 1200ms (observe 900ms, agent-view 150ms)") {
		t.Fatalf("the stages that took 100ms or more, longest first: %v", logged)
	}
	tk.spans = []span{{"observe", 20 * time.Millisecond}}
	tk.logSlow(1500 * time.Millisecond)
	if len(logged) != 2 || !strings.Contains(logged[1], "slow tick: 1500ms (no stage over 100ms)") {
		t.Fatalf("a slow tick with no long stage says so: %v", logged)
	}
}

func TestARunningTickCollectsItsStagesAndLogsTheSlowOne(t *testing.T) {
	tk, _, logs := newTick(t)
	tk.Sweeps.BlockedSweep = func(db.Q, int64) { time.Sleep((SlowTickMs + 50) * time.Millisecond) }
	if _, err := tk.Run(nil); err != nil {
		t.Fatal(err)
	}
	if len(*logs) != 1 || !strings.Contains((*logs)[0], "slow tick:") || !strings.Contains((*logs)[0], "blocked sweep 1") {
		t.Fatalf("one line naming the stage that took the time: %v", *logs)
	}
	*logs = nil
	tk.Sweeps.BlockedSweep = func(db.Q, int64) {}
	if _, err := tk.Run(nil); err != nil || len(*logs) != 0 || len(tk.spans) != 0 {
		t.Fatalf("a quick tick says nothing and keeps nothing: %v %v %v", err, *logs, tk.spans)
	}
}
