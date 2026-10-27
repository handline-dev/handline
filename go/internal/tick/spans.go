package tick

import (
	"fmt"
	"sort"
	"strings"
	"time"
)

// A tick that runs long says where the time went: one log line with the stages that took the most. Without it a daemon that is busy
// answers only to a profiler.
const (
	// SlowTickMs is how long a tick runs before its stages are logged.
	SlowTickMs = 1000
	// SlowStageMs is the least a stage takes to be named in that line.
	SlowStageMs = 100
)

type span struct {
	name string
	took time.Duration
}

// timed runs f and adds it to this tick's stages under name; the same name added again (one per agent) sums.
func (t *Tick) timed(name string, f func()) {
	start := time.Now()
	f()
	took := time.Since(start)
	for i := range t.spans {
		if t.spans[i].name == name {
			t.spans[i].took += took
			return
		}
	}
	t.spans = append(t.spans, span{name, took})
}

// logSlow names the slowest stages when the tick took long, longest first, and clears them for the next tick (their room is kept).
func (t *Tick) logSlow(total time.Duration) {
	defer func() { t.spans = t.spans[:0] }()
	if total < SlowTickMs*time.Millisecond {
		return
	}
	sort.SliceStable(t.spans, func(i, j int) bool { return t.spans[i].took > t.spans[j].took })
	var parts []string
	for _, s := range t.spans {
		if s.took >= SlowStageMs*time.Millisecond {
			parts = append(parts, fmt.Sprintf("%s %dms", s.name, s.took.Milliseconds()))
		}
	}
	if len(parts) == 0 {
		parts = append(parts, fmt.Sprintf("no stage over %dms", SlowStageMs))
	}
	t.logf("  slow tick: %dms (%s)", total.Milliseconds(), strings.Join(parts, ", "))
}
