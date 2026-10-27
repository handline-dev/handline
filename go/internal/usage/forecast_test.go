package usage

import "testing"

func TestTheForecastLeavesWhatTheWeekDoesNotUseAtItsCurrentRate(t *testing.T) {
	// The architect's accounts (T6330): unused percent, hours to the reset, week points per hour over the last 24 h, and the most 5h windows
	// could still use (hours/5 x 100 x ratio) as a ratio of 0.1368 would give for gamma.
	for _, c := range []struct {
		name                string
		unused, hours, rate float64
		ratio               float64
		left, lost          float64
	}{
		{"alpha", 71, 95, 0.46, 0.1168, 27.3, 0},
		{"beta", 55, 66, 0.67, 0.1409, 10.8, 0},
		{"gamma", 35, 19, 0.46, 0.1368, 26.3, 0},
		{"sigma", 45, 37, 1.01, 0.1380, 7.6, 0},
		{"theta", 32, 14, 0.46, 0.1179, 25.6, 0},
		{"more unused than full windows could use", 60, 10, 0.2, 0.1, 58, 40},
		{"a week used faster than it lasts", 20, 10, 5, 1, 0, 0},
	} {
		f := ForecastOf(c.unused, c.hours, c.rate*24, c.ratio, true)
		if f.LeftAtReset == nil || f.LostAnyway == nil || *f.LeftAtReset < c.left-0.1 || *f.LeftAtReset > c.left+0.1 ||
			*f.LostAnyway < c.lost-0.1 || *f.LostAnyway > c.lost+0.1 {
			t.Errorf("%s: left %v lost %v, want %.1f and %.1f", c.name, f.LeftAtReset, f.LostAnyway, c.left, c.lost)
		}
	}
	if f := ForecastOf(30, 10, 24, 0, false); f.LeftAtReset == nil || f.LostAnyway != nil {
		t.Errorf("without a ratio only the rate is forecast: %+v", f)
	}
}

func TestAWeekThatCannotBeReadOrHasNoSamplesIsNotForecast(t *testing.T) {
	q := openDB(t)
	home := t.TempDir()
	e := testEnv(home, clock)
	weekAt(t, home, "a", 40)
	if _, ok := e.ForecastFor(q, "a", nil); ok {
		t.Fatal("no samples of the account in the last day: nothing to base a rate on")
	}
	if _, ok := e.ForecastFor(q, "nobody", nil); ok {
		t.Fatal("an unread account has no forecast")
	}
}
