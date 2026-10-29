package staymove

import (
	"strings"
	"testing"
)

func f(v float64) *float64 { return &v }

const (
	window5h = 5 * 3600.0
	hour     = 3600.0
	minute   = 60.0
)

func TestTheWallChoiceInScenarios(t *testing.T) {
	for _, c := range []struct {
		name string
		s    Situation
		move bool
		why  string
	}{
		// alpha at 100%, reset in 33m, a cold start measured at 0, the session closed since 23:49: the room test then finds delta.
		{"a full account, a free start, a closed session", Situation{WorkWaiting: true, ResetInS: f(33 * minute), ColdStartPct: f(0),
			WindowS: window5h, TurnEndedS: f(157 * minute), CacheLifeS: hour}, true, "prompt cache is cold"},
		{"a free start and a warm cache", Situation{WorkWaiting: true, ResetInS: f(33 * minute), ColdStartPct: f(0), WindowS: window5h,
			TurnEndedS: f(minute), CacheLifeS: hour}, true, "resets too late"},
		{"never measured, a warm cache: the default T waits", Situation{WorkWaiting: true, ResetInS: f(33 * minute), WindowS: window5h,
			TurnEndedS: f(minute), CacheLifeS: hour}, false, "within 39m"},
		{"a dear start and a warm cache", Situation{WorkWaiting: true, ResetInS: f(33 * minute), ColdStartPct: f(13), WindowS: window5h,
			TurnEndedS: f(minute), CacheLifeS: hour}, false, "cheaper to wait"},
		{"a start dearer than the cache lives is capped at its life", Situation{WorkWaiting: true, ResetInS: f(50 * minute),
			ColdStartPct: f(40), WindowS: window5h, CacheLifeS: hour}, false, "within 60m"},
		{"the cache's age decides past the cap", Situation{WorkWaiting: true, ResetInS: f(50 * minute), ColdStartPct: f(40),
			WindowS: window5h, TurnEndedS: f(11 * minute), CacheLifeS: hour}, true, "prompt cache is cold"},
		{"nothing waits", Situation{ResetInS: f(5 * hour), ColdStartPct: f(0), WindowS: window5h, TurnEndedS: f(3 * hour),
			CacheLifeS: hour}, false, "no work is waiting"},
		{"an unknown reset", Situation{WorkWaiting: true, WindowS: window5h, TurnEndedS: f(minute), CacheLifeS: hour}, true,
			"reset is unknown"},
	} {
		move, why := Decide(c.s)
		if move != c.move || !strings.Contains(why, c.why) {
			t.Errorf("%s: move %v %q", c.name, move, why)
		}
	}
}

func TestOnlyANeverMeasuredStartTakesTheDefault(t *testing.T) {
	for _, c := range []struct {
		pct     *float64
		windowS float64
		want    float64
	}{{nil, window5h, DefaultStayWithinS}, {f(0), window5h, 0}, {f(1), window5h, 180}, {f(5), 0, DefaultStayWithinS}} {
		if got := StayWithin(c.pct, c.windowS); got != c.want {
			t.Errorf("StayWithin(%v, %v) = %v, want %v", c.pct, c.windowS, got, c.want)
		}
	}
}
