package reads

import (
	"time"

	"github.com/handline/handline/go/internal/usage"
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
