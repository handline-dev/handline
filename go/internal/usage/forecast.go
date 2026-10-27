package usage

import (
	"math"

	"github.com/handline/handline/go/internal/db"
)

// Forecast is what an account's week will have left unused at its reset: left at the rate of the last 24 hours, and the part of
// it that even full 5h windows could not use. A nil figure is not known: nothing is guessed from a week or a ratio that cannot be read.
type Forecast struct{ LeftAtReset, LostAnyway *float64 }

// MinSpanS is the least time the samples must cover for their rate to be a rate: under it a rise is one reading's noise.
const MinSpanS = 2 * 3600

// ForecastOf is the forecast for a week with unused percent left and hoursLeft to its end. ratePerH is the week's points used per hour
// lately; ratio is the week's points per point of the 5h window (Costs.LongPerShort), haveRatio false when it is unknown.
func ForecastOf(unused, hoursLeft, ratePerH, ratio float64, haveRatio bool) Forecast {
	left := math.Max(0, unused-ratePerH*hoursLeft)
	f := Forecast{LeftAtReset: &left}
	if haveRatio {
		lost := math.Max(0, unused-hoursLeft/5*100*ratio)
		f.LostAnyway = &lost
	}
	return f
}

// ForecastFor reads the account's week and, from samples (rows covering the last 24 hours), forecasts it; ok false when the week is stale,
// unread, over, or the account's samples in the last day are fewer than two or span under MinSpanS: a rate needs a span to be one.
func (e *Env) ForecastFor(q db.Q, acct string, rows Samples) (Forecast, bool) {
	now := e.now()
	st := e.Read(acct)
	used, left := st.LongTrusted(), 0.0
	if used == nil {
		return Forecast{}, false
	}
	var ok bool
	if left, ok = WeekEndsIn(st, now); !ok || left <= 0 {
		return Forecast{}, false
	}
	rate, ok := weekRatePerH(rows, acct, int64(now)-86400, int64(now))
	if !ok {
		return Forecast{}, false
	}
	var ratio float64
	var haveRatio bool
	if costs, err := e.LoadCosts(q); err == nil {
		ratio, haveRatio = costs.LongPerShort(acct)
	}
	return ForecastOf(float64(max(0, LongBlock-*used)), left/3600, rate, ratio, haveRatio), true
}

// weekRatePerH is the account's weekly points used per hour over its samples in [lo, hi], divided by the time they cover, not by the whole
// window; ok false with fewer than two or a span under MinSpanS.
func weekRatePerH(rows []sampleRow, acct string, lo, hi int64) (float64, bool) {
	var pts []weekPoint
	for _, r := range rows {
		if w, ok := weekOf(r); ok && w.TS >= lo && w.TS <= hi {
			if a, _ := r["account"].(string); a == acct {
				pts = append(pts, w)
			}
		}
	}
	if len(pts) < 2 || pts[len(pts)-1].TS-pts[0].TS < MinSpanS {
		return 0, false
	}
	var used float64
	for i := 1; i < len(pts); i++ {
		used += rise(pts[i-1], pts[i])
	}
	return used / (float64(pts[len(pts)-1].TS-pts[0].TS) / 3600), true
}
