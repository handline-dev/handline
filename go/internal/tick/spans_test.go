package tick

import (
	"strings"
	"testing"
	"time"
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
	if len(logged) != 0 || tk.spans != nil {
		t.Fatalf("a quick tick logs nothing and clears its stages: %v %v", logged, tk.spans)
	}
	tk.spans = []span{{"agent-view", 150 * time.Millisecond}, {"observe", 900 * time.Millisecond}, {"reconcile", 20 * time.Millisecond}}
	tk.logSlow(1200 * time.Millisecond)
	if len(logged) != 1 || !strings.Contains(logged[0], "slow tick: 1200ms (observe 900ms, agent-view 150ms)") {
		t.Fatalf("the stages that took 100ms or more, longest first: %v", logged)
	}
}
