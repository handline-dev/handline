package usage

import (
	"encoding/json"
	"math"
	"testing"
)

func headroomRow(a string, ts int64, five float64, fiveReset int64, week float64, weekReset int64) sampleRow {
	w := func(m int, pct float64, resets int64) map[string]any {
		return map[string]any{"minutes": m, "used_percent": pct, "resets_at": resets, "observed_at": ts, "carried": false, "lost": false}
	}
	b, _ := json.Marshal([]any{w(300, five, fiveReset), w(10080, week, weekReset)})
	return sampleRow{"account": a, "ts": json.Number(itoa(int(ts))), "windows": string(b)}
}

func pointAt(t *testing.T, ps []HeadroomPoint, at int64) HeadroomPoint {
	t.Helper()
	for _, p := range ps {
		if p.At == at {
			return p
		}
	}
	t.Fatalf("no point at %d", at)
	return HeadroomPoint{}
}

func near(got *float64, want float64) bool { return got != nil && math.Abs(*got-want) < 1e-9 }

// a uses its 5h window 10 points an hour and resets in 2 hours, its week stays at 20 used: ahead the 5h line falls, jumps back to 100 at
// each reset 5 hours apart and falls again. b's last reading is 3 hours old: no guess, and the average ahead leaves it out. c ends in an
// hour: nothing left after it.
func TestTheFiveHourGuessIsASawThatResetsEveryFiveHours(t *testing.T) {
	now := int64(1_800_000_000)
	reset, week := now+2*3600, now+2*86400
	var rows []sampleRow
	for i := int64(0); i <= 6; i++ {
		ts := now - 3*3600 + i*1800
		rows = append(rows, headroomRow("a", ts, float64(i)*5, reset, 20, week), headroomRow("c", ts, 50, reset, 50, week))
	}
	rows = append(rows, headroomRow("b", now-3*3600, 10, reset, 10, week))
	h := HeadroomOver(rows, now, now-3*3600, now+12*3600, 1800, map[string]float64{"c": float64(now + 3600)})
	if len(h.Accounts) != 3 || h.Accounts[0].Account != "a" || len(h.Past) != 7 || len(h.Future) != 24 {
		t.Fatalf("%d accounts, %d past, %d future", len(h.Accounts), len(h.Past), len(h.Future))
	}
	a, b, c := h.Accounts[0], h.Accounts[1], h.Accounts[2]
	for at, want := range map[int64]float64{now: 70, now + 3600: 60, reset: 100, reset + 3600: 90, reset + 4*3600: 60, reset + 5*3600: 100} {
		p := pointAt(t, append(a.Past, a.Future...), at)
		if !near(p.Five, want) || !near(p.Week, 80) {
			t.Errorf("a at now%+ds: five %v week %v, want %v 80", at-now, p.Five, p.Week, want)
		}
	}
	if p := pointAt(t, b.Past, now-3*3600+3600); !near(p.Five, 90) {
		t.Errorf("b an hour after its reading: %v", p.Five)
	}
	if p := pointAt(t, b.Past, now); p.Five != nil {
		t.Errorf("b's reading is too old to say what it holds now: %v", *p.Five)
	}
	if p := pointAt(t, b.Future, now+1800); p.Five != nil {
		t.Errorf("b is stale and has a guess: %v", *p.Five)
	}
	if p := pointAt(t, c.Future, now+3600); !near(p.Five, 0) || !near(p.Week, 0) {
		t.Errorf("c after its end: %v %v", p.Five, p.Week)
	}
	// now+30m: a 65 (its week 80), c 50 (pace 0): b left out.
	if p := h.Future[0]; p.At != now+1800 || !near(p.Left, (65+50)/2.0) {
		t.Errorf("combined ahead: %d %v", p.At-now, p.Left)
	}
	if p := h.Past[len(h.Past)-1]; p.At != now || !near(p.Left, (70+50)/2.0) {
		t.Errorf("combined now: %v", p.Left)
	}
	var fives []int64
	for _, r := range a.Resets {
		if r.Window == "5h" {
			fives = append(fives, r.At-reset)
		}
	}
	if len(fives) != 3 || fives[0] != 0 || fives[1] != 5*3600 || fives[2] != 10*3600 {
		t.Errorf("a's 5h resets ahead, from its reset: %v", fives)
	}
	if len(c.Resets) != 0 {
		t.Errorf("c ends before its reset: %+v", c.Resets)
	}
}

// A window that reset between two readings counts what the new one shows as used; the same reset read by several samples, one a second
// apart, is one reset. A busy window more than a day back does not set the 5h pace.
func TestThePaceCountsANewWindowFromNothingAndAResetIsListedOnce(t *testing.T) {
	now := int64(1_800_000_000)
	old, cur, week := now-5400, now-5400+5*3600, now+86400
	rows := []sampleRow{
		headroomRow("a", now-30*3600, 0, now-26*3600, 10, week),
		headroomRow("a", now-27*3600, 90, now-26*3600, 10, week),
		headroomRow("a", now-150*60, 30, old+1, 10, week),
		headroomRow("a", now-2*3600, 40, old, 10, week),
		headroomRow("a", now-3600, 5, cur, 10, week+1),
		headroomRow("a", now, 15, cur-1, 10, week),
	}
	h := HeadroomOver(rows, now, now-2*3600, now+3600, 3600, nil)
	a := h.Accounts[0]
	// 10, 5 then 10 points over 2.5 hours: in an hour the 5h window has 85 - 10 left.
	if p := pointAt(t, a.Future, now+3600); !near(p.Five, 75) {
		t.Errorf("five ahead: %v", *p.Five)
	}
	if len(a.Resets) != 1 || a.Resets[0].At != old || a.Resets[0].Window != "5h" {
		t.Errorf("resets: %+v", a.Resets)
	}
}

// With no reading there are no accounts, and the average still has every point, with nothing in it.
func TestHeadroomWithNoReadingHasNoAccountsAndEmptyPoints(t *testing.T) {
	h := HeadroomOver(nil, 1_800_000_000, 1_800_000_000-3600, 1_800_000_000+3600, 900, nil)
	if len(h.Accounts) != 0 || len(h.Past) != 5 || len(h.Future) != 4 || h.Past[0].Left != nil {
		t.Fatalf("%+v", h)
	}
}

// fast spent 60 of its week in the last day, half way through it: at that rate it hits the wall in under a day. Paced, it goes at the
// rate that reaches 98 at its reset, and its 5h window at that over its weekly-to-5h ratio. Unpaced, at its last day's use.
func TestThePacedGuessHoldsTheWeekToItsLineAndTheFiveHourWindowToItsShare(t *testing.T) {
	now := int64(1_800_000_000)
	reset := now + WeekS/2
	var rows []sampleRow
	for i := int64(0); i <= 24; i++ {
		ts := now - 86400 + i*3600
		rows = append(rows, headroomRow("fast", ts, float64(i)*2, now+2*3600, 20+float64(i)*2.5, reset))
	}
	paced := Paced{On: true, Target: 98, Ratio: func(string) (float64, bool) { return 0.13, true }}
	h := HeadroomPaced(rows, now, now-3600, reset+3600, 3600, nil, paced)
	a := h.Accounts[0]
	if p := pointAt(t, a.Future, reset-3600+(now%3600)); p.Week == nil || *p.Week < 2-0.5 || *p.Week > 2+0.5 {
		t.Errorf("paced: the week ends near 100-98 left: %v", p.Week)
	}
	if p := pointAt(t, a.Past, now); !near(p.Line, 100-49) {
		t.Errorf("the line half way through the week: %v, want 51 left", p.Line)
	}
	weekRate := (98.0 - 80) / float64(reset-now)
	if p := pointAt(t, a.Future, now+3600); !near(p.Five, 100-48-weekRate/0.13*3600) {
		t.Errorf("the 5h window goes at the paced week rate over the ratio: %v, want %v", *p.Five, 100-48-weekRate/0.13*3600)
	}
	paced.On = false
	h = HeadroomPaced(rows, now, now-3600, reset+3600, 3600, nil, paced)
	if p := pointAt(t, h.Accounts[0].Future, now+8*3600); !near(p.Week, 0) {
		t.Errorf("unpaced: at 2.5 an hour the week is gone in 8 hours: %v", p.Week)
	}
	if p := pointAt(t, h.Accounts[0].Future, now+3600); p.Line != nil {
		t.Errorf("no line with pacing off: %v", *p.Line)
	}
	if p := pointAt(t, h.Accounts[0].Past, now); p.Line != nil {
		t.Errorf("no line looking back with pacing off: %v", *p.Line)
	}
}

// slow's week resets in a day; it used 40 points in the last day, faster than the line. Two days past the reset its new week goes at
// the line's rate, not its last day's, and the line starts again from that reset.
func TestPastTheWeeklyResetThePacedGuessAndTheLineStartAgain(t *testing.T) {
	now := int64(1_800_000_000)
	reset := now + 86400
	var rows []sampleRow
	for i := int64(0); i <= 24; i++ {
		rows = append(rows, headroomRow("slow", now-86400+i*3600, 5, now+3600, 40+float64(i)*40/24, reset))
	}
	paced := Paced{On: true, Target: 98, Ratio: func(string) (float64, bool) { return 0, false }}
	h := HeadroomPaced(rows, now, now, reset+3*86400, 3600, nil, paced)
	p := pointAt(t, h.Accounts[0].Future, reset+2*86400)
	if want := 100 - 98*2.0/7; !near(p.Week, want) {
		t.Errorf("the week after the reset goes at 98 over the week: %v, want %v", *p.Week, want)
	}
	if want := 100 - 98*2.0/7; !near(p.Line, want) {
		t.Errorf("the line restarts at the reset: %v, want %v", *p.Line, want)
	}
}

// Before its week starts the line leaves all of it, after its end what Target leaves.
func TestTheLineHoldsBeforeItsWeekAndAfterItsEnd(t *testing.T) {
	p := Paced{On: true, Target: 98}
	reset := int64(1_800_000_000)
	if got := p.lineLeft(reset-WeekS-3600, reset, 0); !near(got, 100) {
		t.Errorf("before the week: %v", *got)
	}
	if got := p.lineLeft(reset-3600, reset, float64(reset-86400)); !near(got, 2) {
		t.Errorf("after an end before the reset: %v", *got)
	}
}

// A reading hours old, or one at the wall, keeps its figure until its reset instead of leaving the guess.
func TestAStaleAccountKeepsItsLastReadingUntilItsReset(t *testing.T) {
	now := int64(1_800_000_000)
	reset := now + 86400
	rows := []sampleRow{headroomRow("walled", now-10*3600, 100, now-5*3600, 99, reset)}
	h := HeadroomPaced(rows, now, now-3600, now+2*86400, 3600, nil, Paced{On: true, Target: 98, Ratio: func(string) (float64, bool) { return 0, false }})
	f := h.Accounts[0].Future
	if p := pointAt(t, f, now+3600); !near(p.Week, 1) {
		t.Errorf("before its reset it keeps 99 used: %v", p.Week)
	}
	if p := pointAt(t, f, reset+3600); !near(p.Week, 100) {
		t.Errorf("after its reset it starts from nothing: %v", p.Week)
	}
}

// The backtest: an account the daemon holds to its line (its use wanders a few points around it within a day) is guessed two, three and
// four days into its week, and each guess is held to what the rest of the week recorded.
func TestBacktestAPacedAccountsGuessFollowsWhatItThenUsed(t *testing.T) {
	start := int64(1_800_000_000)
	reset := start + WeekS
	usedAt := func(ts int64) float64 {
		share := float64(ts-start) / WeekS
		return max(0, 98*share+3*math.Sin(float64(ts-start)/3000))
	}
	var rows []sampleRow
	for ts := start; ts < reset; ts += 1800 {
		rows = append(rows, headroomRow("a", ts, 20, ts+3600, usedAt(ts), reset))
	}
	paced := Paced{On: true, Target: 98, Ratio: func(string) (float64, bool) { return 0.13, true }}
	for _, at := range []int64{start + 2*86400, start + 3*86400, start + 4*86400} {
		var seen []sampleRow
		for _, r := range rows {
			if ts, _ := num(r["ts"]); int64(ts) <= at {
				seen = append(seen, r)
			}
		}
		h := HeadroomPaced(seen, at, at, reset-1800, 1800, nil, paced)
		worst := 0.0
		for _, p := range h.Accounts[0].Future {
			worst = max(worst, math.Abs(*p.Week-(100-usedAt(p.At))))
		}
		if worst > 8 {
			t.Errorf("guessed %d days in: off by up to %.1f points of the week, want at most 8", (at-start)/86400, worst)
		}
	}
}
