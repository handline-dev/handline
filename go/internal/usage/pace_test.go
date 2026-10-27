package usage

import (
	"os"
	"path/filepath"
	"testing"
)

// accountAt writes account "a" with its 5h window at shortUsed percent resetting in shortMin minutes and its week at longUsed resetting in
// longS seconds.
func accountAt(t *testing.T, home string, shortUsed int, shortMin int64, longUsed int, longS int64) {
	t.Helper()
	writeStatus(t, home, "a", map[string]any{"collected_at": clock - 5, "windows": []any{
		map[string]any{"minutes": 300, "used_percent": shortUsed, "resets_at": clock + shortMin*60, "observed_at": clock - 5},
		map[string]any{"minutes": 10080, "used_percent": longUsed, "resets_at": clock + longS, "observed_at": clock - 5}}})
}

// weekAt writes an account whose week is half over (it resets in half a week) with the given weekly figure.
func weekAt(t *testing.T, home, acct string, used int) {
	t.Helper()
	writeStatus(t, home, acct, map[string]any{"collected_at": clock - 5, "windows": []any{
		map[string]any{"minutes": 300, "used_percent": 10, "resets_at": clock + 3600, "observed_at": clock - 5},
		map[string]any{"minutes": 10080, "used_percent": used, "resets_at": clock + WeekS/2, "observed_at": clock - 5}}})
}

func TestTheBucketHoldsTheWeeksElapsedShareAndABurstLessWhatIsSpent(t *testing.T) {
	home := t.TempDir()
	e := testEnv(home, clock)
	weekAt(t, home, "a", 50)
	h, ok := e.Headroom("a")
	if !ok || h < 0.0999 || h > 0.1001 {
		t.Fatalf("half the week and a 10%% burst, 50%% spent: %v %v", h, ok)
	}
	weekAt(t, home, "a", 60)
	if h, _ := e.Headroom("a"); h < -0.0001 || h > 0.0001 {
		t.Fatalf("spending the burst empties the bucket: %v", h)
	}
	writeStatus(t, home, "a", map[string]any{"collected_at": clock - 5, "windows": []any{}})
	if _, ok := e.Headroom("a"); ok {
		t.Fatal("a weekly figure nobody can read says nothing about pace")
	}
}

func TestWithPacingOffAFastPaceWithLowCutoffRiskKeepsTheFullCap(t *testing.T) {
	q := openDB(t)
	home := t.TempDir()
	e := testEnv(home, clock)
	writeConfig(t, home, map[string]any{"active_cap": 4, "pacing": false})
	for _, acct := range []string{"ahead", "burning"} {
		os.MkdirAll(filepath.Join(home, "accounts", acct), 0o755)
		weekAt(t, home, acct, 80) // far ahead of its week: 80% spent half way through
	}
	// 5h at 10%, an hour to its reset: 0.01%/min lasts far past it.
	if err := WriteBurn(home, "burning", 0.01/60, clock); err != nil {
		t.Fatal(err)
	}
	loads, err := e.ReadAll(q, "")
	if err != nil {
		t.Fatal(err)
	}
	for _, acct := range []string{"ahead", "burning"} {
		if got := loads[acct].Cap; got != 4 {
			t.Errorf("%s: cap %d, want the configured 4 - with pacing off a fast pace alone is no risk", acct, got)
		}
		if got := e.CapNow(q, acct); got != 4 {
			t.Errorf("%s: CapNow %d, want 4", acct, got)
		}
	}
}

func TestAHighFiveHourBurnDoesNotLowerTheCap(t *testing.T) {
	q := openDB(t)
	home := t.TempDir()
	e := testEnv(home, clock)
	writeConfig(t, home, map[string]any{"active_cap": 4})
	os.MkdirAll(filepath.Join(home, "accounts", "hot"), 0o755)
	weekAt(t, home, "hot", 20) // behind its week
	// 5h at 10%: 90 points left, the reset an hour off. 3%/min reaches the block in 30 minutes, half the way there: a start is held for it
	// (FiveHourShare), the cap is not shrunk.
	if err := WriteBurn(home, "hot", 3.0/60, clock); err != nil {
		t.Fatal(err)
	}
	loads, err := e.ReadAll(q, "")
	if err != nil {
		t.Fatal(err)
	}
	if got := loads["hot"].Cap; got != 4 {
		t.Fatalf("cap %d, want 4: the 5-hour burn is the start's gate, not the cap's", got)
	}
	if got := e.CapNow(q, "hot"); got != 4 {
		t.Fatalf("CapNow %d, want 4", got)
	}
}

func TestCutoffShareJudgesTheWeekAndFiveHourShareTheShortestWindow(t *testing.T) {
	home := t.TempDir()
	e := testEnv(home, clock)
	accountAt(t, home, 50, 60, 90, 86400)
	st, now := e.Read("a"), float64(clock)
	if got := CutoffShare(st, 0, 0, false, now); got != 1 {
		t.Fatalf("no burn, no risk: %v", got)
	}
	// 50 points over an hour: 0.5/60 %/s lasts 100 minutes, past the reset: share 100/60.
	if got, ok := FiveHourShare(st, 0.5/60, 0, now); !ok || got < 1.66 || got > 1.67 {
		t.Fatalf("a burn that outlasts its reset: %v %v", got, ok)
	}
	// One more agent at 1%/min on top: 50 points last 50/1.5 = 33 of the 60 minutes.
	if got, _ := FiveHourShare(st, 0.5/60, 1.0/60, now); got < 0.55 || got > 0.56 {
		t.Fatalf("the newcomer takes it past the wall: %v", got)
	}
	if got, ok := FiveHourShare(st, 0, 0, now); !ok || got != 1 {
		t.Fatalf("no burn, no risk: %v %v", got, ok)
	}
	stale := *st
	stale.Short.Reset = 0
	if _, ok := FiveHourShare(&stale, 1, 1, now); ok {
		t.Fatal("no reset is no projection")
	}
	// The week: 10 points over a day, at a tenth of the shortest window's burn: 0.05%/min runs out in 200 minutes.
	if got := CutoffShare(st, 0.5/60, 0.1, true, now); got < 0.138 || got > 0.139 {
		t.Fatalf("the week cut off first: %v", got)
	}
}

func TestCutoffCapRoundsUpSoAnyShareLeftKeepsOneSlot(t *testing.T) {
	for _, c := range []struct {
		base int
		s    float64
		want int
	}{{2, 2, 2}, {2, 1, 2}, {2, 0.5, 1}, {2, 0.01, 1}, {2, 0, 0}, {3, 0.5, 2}, {4, 0.9, 4}} {
		if got := CutoffCap(c.base, c.s); got != c.want {
			t.Errorf("CutoffCap(%d, %v) = %d, want %d", c.base, c.s, got, c.want)
		}
	}
}

func TestHeadroomIsTheTighterOfThe5hAndTheWeeklyBucket(t *testing.T) {
	home := t.TempDir()
	e := testEnv(home, clock)
	// The week is half over with 20% spent (0.4 of room); the 5h window is a fifth over with 30% spent (0.0 of room).
	writeStatus(t, home, "a", map[string]any{"collected_at": clock - 5, "windows": []any{
		map[string]any{"minutes": 300, "used_percent": 30, "resets_at": clock + 4*3600, "observed_at": clock - 5},
		map[string]any{"minutes": 10080, "used_percent": 20, "resets_at": clock + WeekS/2, "observed_at": clock - 5}}})
	if h, ok := e.Headroom("a"); !ok || h < -0.0001 || h > 0.0001 {
		t.Fatalf("the 5h bucket is the tighter one: %v %v", h, ok)
	}
}

func TestThePaceLineShrinksTheCapInStepWithTheBurstAndStopsItPastIt(t *testing.T) {
	q := openDB(t)
	home := t.TempDir()
	e := testEnv(home, clock)
	writeConfig(t, home, map[string]any{"active_cap": 4})
	os.MkdirAll(filepath.Join(home, "accounts", "a"), 0o755)
	// Half the week gone: the line is at 49 (98 x 1/2).
	for _, c := range []struct {
		used  int
		state string
		cap   int
	}{{40, PaceBehind, 4}, {49, PaceBehind, 4}, {54, PaceAhead, 2}, {58, PaceAhead, 1}, {59, PaceAhead, 1}, {60, PaceOver, 0}} {
		weekAt(t, home, "a", c.used)
		p := e.PaceNow("a")
		if p == nil || p.State != c.state || p.Line != 49 || p.Used != c.used {
			t.Fatalf("used %d: pace %+v, want %s on a line of 49", c.used, p, c.state)
		}
		if got := e.CapNow(q, "a"); got != c.cap {
			t.Errorf("used %d (%s): cap %d, want %d", c.used, c.state, got, c.cap)
		}
		if got := e.CapUnpaced(q, "a"); got != 4 {
			t.Errorf("used %d: unpaced cap %d, want the configured 4", c.used, got)
		}
	}
}

func TestAnAccountOverItsLineIsBackWhenTheLineCatchesUpAndItsRateEndsAtTheTarget(t *testing.T) {
	home := t.TempDir()
	e := testEnv(home, clock)
	os.MkdirAll(filepath.Join(home, "accounts", "a"), 0o755)
	weekAt(t, home, "a", 79)
	p := e.PaceNow("a")
	if p == nil || p.State != PaceOver {
		t.Fatalf("79%% half way through: %+v, want over", p)
	}
	// The line reaches 79-10=69 at 69/98 of the week; half a week is gone.
	start := float64(clock + WeekS/2 - WeekS)
	if want := int64(start + 69.0/98*WeekS + 1); p.BackAt < want-1 || p.BackAt > want {
		t.Errorf("back at %d, want about %d", p.BackAt, want)
	}
	if want := (98.0 - 79) / (WeekS / 2 / 3600); p.RateH < want-1e-9 || p.RateH > want+1e-9 {
		t.Errorf("rate %v, want %v points an hour", p.RateH, want)
	}
	weekAt(t, home, "a", 99)
	if p := e.PaceNow("a"); p.RateH != 0 || p.BackAt == 0 {
		t.Errorf("past the target: rate %v back %d, want 0 and a time", p.RateH, p.BackAt)
	}
	writeConfig(t, home, map[string]any{"pace_burst_pct": 0})
	weekAt(t, home, "a", 100)
	if p := e.PaceNow("a"); p.BackAt != int64(clock+WeekS/2) {
		t.Errorf("a line that never gets there: back %d, want the week's end %d", p.BackAt, int64(clock+WeekS/2))
	}
	// A reading days old still holds until its reset.
	writeStatus(t, home, "a", map[string]any{"collected_at": clock - 2*86400, "windows": []any{
		map[string]any{"minutes": 300, "used_percent": 10, "resets_at": clock - 86400, "observed_at": clock - 2*86400},
		map[string]any{"minutes": 10080, "used_percent": 79, "resets_at": clock + WeekS/2, "observed_at": clock - 2*86400}}})
	if p := e.PaceNow("a"); p == nil || p.Used != 79 {
		t.Errorf("a stale weekly reading: %+v, want it judged", p)
	}
	writeConfig(t, home, map[string]any{"pacing": false})
	if p := e.PaceNow("a"); p == nil || p.State != PaceOff || p.BackAt != 0 {
		t.Errorf("pacing off: %+v, want off with the figures", p)
	}
	writeStatus(t, home, "a", map[string]any{"collected_at": clock - 5, "windows": []any{}})
	if p := e.PaceNow("a"); p != nil {
		t.Errorf("no weekly figure: %+v, want none", p)
	}
}

func TestPaceSettingsOutOfRangeFallBackToTheirDefaults(t *testing.T) {
	if ps := PaceSettingsOf(Config{}); ps != (PaceSettings{On: true, Target: 98, Burst: 10}) {
		t.Errorf("defaults: %+v", ps)
	}
	if ps := PaceSettingsOf(Config{"pacing": false, "pace_target_pct": 120, "pace_burst_pct": 5}); ps != (PaceSettings{Target: 98, Burst: 5}) {
		t.Errorf("set: %+v", ps)
	}
}

// The pace cap is figured on the founder's cap, not on what the cut-off risk left of it, and the smaller of the two is in force.
func TestThePaceCapIsFiguredOnTheFoundersCapAndTheSmallestCapIsInForce(t *testing.T) {
	q := openDB(t)
	home := t.TempDir()
	e := testEnv(home, clock)
	writeConfig(t, home, map[string]any{"active_cap": 4})
	os.MkdirAll(filepath.Join(home, "accounts", "a"), 0o755)
	weekAt(t, home, "a", 56) // 7 points ahead of a line at 49: ceil(4 x 3/10) = 2
	if got := e.CapUnpaced(q, "a"); got != 4 {
		t.Fatalf("the unpaced cap: %d, want the founder's 4", got)
	}
	if got := e.CapNow(q, "a"); got != 2 {
		t.Errorf("cap %d, want 2: the pace on the founder's 4", got)
	}
}

func TestAWeekPastItsResetOrItsSubscriptionsEndIsPacedAsSuch(t *testing.T) {
	now, used := float64(clock), 60
	ps := PaceSettings{On: true, Target: 98, Burst: 10}
	st := &AccountStatus{AsOf: &now, Long: AcctWindow{Minutes: 10080, Pct: &used, Reset: int64(now) - 10}}
	if p := PaceOf(st, now, ps); p != nil {
		t.Errorf("a reading whose reset has passed: %+v, want none", p)
	}
	st.Long.Reset, st.SubEnd = int64(now)+86400, now-3600
	if p := PaceOf(st, now, ps); p == nil || p.Line != 98 || p.RateH != 0 {
		t.Errorf("past its subscription's end the line is at the target: %+v", p)
	}
}

// The 5-hour gate prices the newcomer at its measured cost: 0.159 %/min, never the starting value.
func TestAStartIsHeldOnTheFiveHourWindowOnlyWhenTheMeasuredCostWouldRunItOut(t *testing.T) {
	home := t.TempDir()
	e := testEnv(home, clock)
	measured := &Costs{longRatios: map[string]float64{}}
	for i := 0; i < MinSamples; i++ {
		measured.samples = append(measured.samples, Sample{R: 0.159 / 60})
	}
	for _, c := range []struct {
		name         string
		used, resets int // percent used, minutes to the reset
		fillPerMin   float64
		costs        *Costs
		held         bool
	}{
		{"an empty account early in its window", 0, 280, 0, measured, false},
		{"30% used with 93 minutes left and nothing running", 30, 93, 0, measured, false},
		{"70% used, 2 hours left, 0.2%/min already running", 70, 120, 0.2, measured, true},
		{"a fresh window that three agents are filling fast", 0, 280, 0.45, measured, true},
		{"only the starting value is known: nothing is held on a guess", 0, 280, 0.45, &Costs{longRatios: map[string]float64{}}, false},
	} {
		accountAt(t, home, c.used, int64(c.resets), 5, WeekS/2)
		burn := c.fillPerMin / 60
		ld := &Load{Account: "a", Cap: 4, Burn: &burn, Costs: c.costs}
		pct := c.used
		if why, _ := NoRoom(e.Read("a"), ld, &pct, ""); (why != "") != c.held {
			t.Errorf("%s: %q, held want %v", c.name, why, c.held)
		}
	}
}

func TestTheGateReadsTheMeasuredCostFromTheDatabaseAndHoldsNothingOnTheStartingValue(t *testing.T) {
	q := openDB(t)
	home := t.TempDir()
	e := testEnv(home, clock)
	os.MkdirAll(filepath.Join(home, "accounts", "a"), 0o755)
	accountAt(t, home, 0, 280, 5, WeekS/2)
	if err := WriteBurn(home, "a", 0.45/60, clock); err != nil {
		t.Fatal(err)
	}
	if _, ok := e.FiveHourShareWith(q, "a", ""); ok {
		t.Fatal("no samples: the cost is the starting value and nothing is projected")
	}
	rows := make([]map[string]any, MinSamples)
	for i := range rows {
		rows[i] = map[string]any{"r": 0.159 / 60}
	}
	putMeta(t, q, samplesMeta, rows)
	if share, ok := e.FiveHourShareWith(q, "a", ""); !ok || share < 0.58 || share > 0.60 {
		t.Fatalf("100 / ((0.45 + 0.159) x 280) = 0.59, got %v %v", share, ok)
	}
}
