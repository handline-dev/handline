package reads

import (
	"time"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/usage"
)

const (
	headroomSpanS    = 3 * 86400 // from and to default to this far either side of now
	headroomStepS    = 900
	headroomMinStepS = 60
	headroomPoints   = 2000 // at most this many points per line
)

// optTime is an RFC 3339 timestamp under key, as Unix seconds; nil when absent.
func optTime(params map[string]any, key string) (*int64, error) {
	s, err := optStr(params, key)
	if err != nil || s == nil {
		return nil, err
	}
	t, perr := time.Parse(time.RFC3339, *s)
	if perr != nil {
		return nil, badParam(key+" is an RFC 3339 timestamp", key)
	}
	u := t.Unix()
	return &u, nil
}

// statsHeadroom is stats.headroom: each account's 5h and weekly headroom, recorded then guessed, their resets, and the average of
// what binds.
func (e *Env) statsHeadroom(_ *rpc.Conn, params map[string]any) (any, error) {
	clock := time.Now()
	now := clock.Unix()
	from, to, step := now-headroomSpanS, now+headroomSpanS, int64(headroomStepS)
	for key, into := range map[string]*int64{"from": &from, "to": &to} {
		v, err := optTime(params, key)
		if err != nil {
			return nil, err
		}
		if v != nil {
			*into = *v
		}
	}
	if v, err := optInt(params, "step_s"); err != nil {
		return nil, err
	} else if v != nil {
		step = *v
	}
	switch {
	case to <= from:
		return nil, badParam("to is after from", "to")
	case step < headroomMinStepS:
		return nil, badParam("step_s is at least 60", "step_s")
	case (to-from)/step+1 > headroomPoints:
		return nil, badParam("step_s is too small for the span: at most 2000 points", "step_s")
	}
	return e.headroomAt(clock, from, to, step)
}

func (e *Env) headroomAt(clock time.Time, from, to, step int64) (any, error) {
	now := clock.Unix()
	rows, err := usage.SamplesSince(e.DB, min(from, now-86400)-usage.OutlookMaxAgeS)
	if err != nil {
		return nil, err
	}
	ue := &usage.Env{Home: e.Home, Now: func() float64 { return float64(now) }}
	h := usage.HeadroomOver(rows, now, from, to, step, e.accountEnds(ue.CurrentConfig(), rows, clock))
	accounts := []any{}
	var next any
	var nextAt int64
	for _, a := range h.Accounts {
		resets := []any{}
		for _, r := range a.Resets {
			resets = append(resets, map[string]any{"at": ts(r.At), "window": r.Window})
			if r.At > now && (nextAt == 0 || r.At < nextAt) {
				nextAt, next = r.At, map[string]any{"at": ts(r.At), "account": a.Account, "window": r.Window}
			}
		}
		var left, lost any
		if f, ok := ue.ForecastFor(e.DB, a.Account, rows); ok {
			left, lost = rounded(f.LeftAtReset), rounded(f.LostAnyway)
		}
		accounts = append(accounts, map[string]any{"account": a.Account, "past": headroomWindows(a.Past, false), "future": headroomWindows(a.Future, false),
			"resets": resets, "left_at_reset": left, "lost_anyway": lost})
	}
	var runsOut any
	for _, p := range h.Future {
		if p.Left != nil && *p.Left <= 0 {
			runsOut = ts(p.At)
			break
		}
	}
	return map[string]any{"now": ts(now), "step_s": step, "combined": map[string]any{"past": points(h.Past), "future": points(h.Future)},
		"accounts": accounts, "runs_out_at": runsOut, "next_reset": next}, nil
}

// headroomWindows is each point's windows left, at two places, and with line its pace line's.
func headroomWindows(ps []usage.HeadroomPoint, line bool) []any {
	left := func(v *float64) any {
		if v == nil {
			return nil
		}
		return round(*v, 2)
	}
	out := make([]any, 0, len(ps))
	for _, p := range ps {
		m := map[string]any{"at": ts(p.At), "five_left": left(p.Five), "week_left": left(p.Week)}
		if line {
			m["line_left"] = left(p.Line)
		}
		out = append(out, m)
	}
	return out
}

// rounded is v to one decimal, nil for an unknown figure.
func rounded(v *float64) any {
	if v == nil {
		return nil
	}
	return round(*v, 1)
}
