package usage

import (
	"math"

	"github.com/handline/handline/go/internal/db"
)

// Forecast is what an account's week will have left unused at its reset (T6330): left at the rate of the last 24 hours, and the part of
// it that even full 5h windows could not use. A nil figure is not known: nothing is guessed from a week or a ratio that cannot be read.
type Forecast struct {
	LeftAtReset, LostAnyway *float64
	HoursLeft               float64 // to the week's end, set by ForecastFor
}

// ForecastOf is the forecast for a week with unused percent left and hoursLeft to its end. consumed24 is the week's points used in the
// last 24 hours; ratio is the week's points per point of the 5h window (Costs.LongPerShort), haveRatio false when it is unknown.
func ForecastOf(unused, hoursLeft, consumed24, ratio float64, haveRatio bool) Forecast {
	left := math.Max(0, unused-consumed24/24*hoursLeft)
	f := Forecast{LeftAtReset: &left}
	if haveRatio {
		lost := math.Max(0, unused-hoursLeft/5*100*ratio)
		f.LostAnyway = &lost
	}
	return f
}

// ForecastFor reads the account's week and, from samples (rows covering the last 24 hours), forecasts it; ok false when the week is stale,
// unread, over, or the samples say nothing of the account in the last day.
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
	consumed, seen := WeekConsumed(rows, int64(now)-86400, int64(now))[acct]
	if !seen {
		return Forecast{}, false
	}
	var ratio float64
	var haveRatio bool
	if costs, err := e.LoadCosts(q); err == nil {
		ratio, haveRatio = costs.LongPerShort(acct)
	}
	f := ForecastOf(float64(max(0, LongBlock-*used)), left/3600, consumed, ratio, haveRatio)
	f.HoursLeft = left / 3600
	return f, true
}
