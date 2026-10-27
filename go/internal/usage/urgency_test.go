package usage

import "testing"

func TestUrgencyIsTheUnusedWeekPerHourLeft(t *testing.T) {
	home := t.TempDir()
	e := testEnv(home, clock)
	week := func(acct string, used int, hoursLeft float64) {
		writeStatus(t, home, acct, map[string]any{"collected_at": clock - 5, "windows": []any{
			map[string]any{"minutes": 300, "used_percent": 10, "resets_at": clock + 3600, "observed_at": clock - 5},
			map[string]any{"minutes": 10080, "used_percent": used, "resets_at": clock + int64(hoursLeft*3600), "observed_at": clock - 5}}})
	}
	week("alpha", 28, 95)
	week("theta", 67, 14)
	if u, ok := Urgency(e.Read("alpha")); !ok || u < 0.75 || u > 0.77 {
		t.Errorf("alpha: 72%% unused over 95h is 0.76%%/h, got %v %v", u, ok)
	}
	if u, ok := Urgency(e.Read("theta")); !ok || u < 2.3 || u > 2.4 {
		t.Errorf("theta: 33%% unused over 14h is 2.35%%/h, got %v %v", u, ok)
	}
	if _, ok := Urgency(e.Read("nobody")); ok {
		t.Error("no weekly reading is not ranked")
	}
}

func TestTheWeekThatRunsOutFirstIsSpentFirstAndSimilarOnesSpread(t *testing.T) {
	// The architect's numbers (T6323): unused weekly use per hour left.
	all := []Ranked{{"alpha", 0.76, true, 0}, {"beta", 0.83, true, 0}, {"delta", 1.00, true, 0}, {"gamma", 1.88, true, 1}, {"sigma", 1.24, true, 0}, {"theta", 2.34, true, 2}}
	for _, c := range []struct {
		name string
		in   []Ranked
		want string
	}{
		{"the most urgent wins beyond the band, however busy", all, "theta"},
		{"without it, gamma", all[:5], "gamma"},
		{"a similar account with fewer agents mid-turn wins (1.86 is inside gamma's 0.28 band)", []Ranked{{"gamma", 1.88, true, 2}, {"sigma", 1.86, true, 0}, {"alpha", 0.76, true, 0}}, "sigma"},
		{"the same agents: the more urgent wins", []Ranked{{"gamma", 1.88, true, 1}, {"sigma", 1.86, true, 1}}, "gamma"},
		{"the band is at least 0.2 points: 1.0 and 0.85 are similar", []Ranked{{"a", 1.0, true, 3}, {"b", 0.85, true, 0}}, "b"},
		{"and not wider: 1.0 and 0.7 are not", []Ranked{{"a", 1.0, true, 3}, {"b", 0.7, true, 0}}, "a"},
		{"an account whose week cannot be read is not ranked", []Ranked{{"a", 0.3, true, 5}, {"unread", 0, false, 0}}, "a"},
	} {
		if got, ok := BestByUrgency(c.in); !ok || got.Name != c.want {
			t.Errorf("%s: %q %v, want %q", c.name, got.Name, ok, c.want)
		}
	}
	if _, ok := BestByUrgency([]Ranked{{"a", 0, false, 0}}); ok {
		t.Error("with no week readable the caller's older key decides")
	}
}
