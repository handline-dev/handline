package usage

import "sort"

// The headroom of each account over a span, its 5h and weekly windows apart (stats.headroom): what usage_samples recorded up to now, and
// a guess after it. The guess keeps each window's pace and starts a window again from nothing at its reset, so the 5h line is a saw.

const (
	fiveS = 5 * 3600
	// headroomPaceS: the 5h pace is what was used over the last day, so one busy or idle window does not set it.
	headroomPaceS = 86400
)

// HeadroomPoint is what an account had left at a moment, in percent; nil where it had no reading. Line is what its pace line leaves of
// the week (Paced only).
type HeadroomPoint struct {
	At               int64
	Five, Week, Line *float64
}

// Left is the headroom that binds: the smaller of the two windows.
func (p HeadroomPoint) Left() *float64 {
	if p.Five == nil || p.Week == nil {
		return nil
	}
	v := min(*p.Five, *p.Week)
	return &v
}

// HeadroomReset is a window's reset: Window is "5h" or "7d".
type HeadroomReset struct {
	At     int64
	Window string
}

// AccountHeadroom is one account's points, past then future, and its resets in the span, in time order.
type AccountHeadroom struct {
	Account      string
	Past, Future []HeadroomPoint
	Resets       []HeadroomReset
}

// HeadroomSpan is every account's, and their average at each point (Left nil where no account had a value).
type HeadroomSpan struct {
	Accounts     []AccountHeadroom
	Past, Future []OutlookPoint
}

// windowPoint is one window of a sample: when, the percent used, and when it resets (0 unknown).
type windowPoint struct {
	TS, Reset int64
	Used      float64
}

// windowsOf is a sample's 5h window (the shortest of 5 hours or less) and its weekly one (the longest above), as weekOf reads them.
func windowsOf(r sampleRow) (five, week windowPoint, hasFive, hasWeek bool) {
	ts, ok := num(r["ts"])
	if !ok {
		return
	}
	for _, e := range entries(r) {
		if e["used_percent"] == nil || truthy(e["lost"]) {
			continue
		}
		if v, has := e["readable"]; has && !truthy(v) {
			continue
		}
		used, ok := num(e["used_percent"])
		m, isM := isInt(e["minutes"])
		if !ok || !isM {
			continue
		}
		var reset int64
		if p := asInt(e["resets_at"]); p != nil {
			reset = (*p + 30) / 60 * 60 // the same reset comes back a second apart
		}
		w := windowPoint{int64(ts), reset, used}
		switch {
		case m <= 300 && !hasFive:
			five, hasFive = w, true
		case m > 300:
			week, hasWeek = w, true
		}
	}
	return
}

// windowAt is the latest point at or before t, unless it is too old to say what the account held.
func windowAt(series []windowPoint, t int64) (windowPoint, bool) {
	i := sort.Search(len(series), func(i int) bool { return series[i].TS > t })
	if i == 0 || t-series[i-1].TS > OutlookMaxAgeS {
		return windowPoint{}, false
	}
	return series[i-1], true
}

// usedPerS is the percent used per second from since to the last point: what each step added within one window, and what a new window
// shows at its first point; 0 over less than OutlookMinSpanS.
func usedPerS(series []windowPoint, since int64) float64 {
	i := sort.Search(len(series), func(i int) bool { return series[i].TS >= since })
	if i >= len(series) || series[len(series)-1].TS-series[i].TS < OutlookMinSpanS {
		return 0
	}
	var used float64
	for j := i + 1; j < len(series); j++ {
		if series[j].Reset == series[j-1].Reset {
			used += max(0, series[j].Used-series[j-1].Used)
		} else {
			used += series[j].Used
		}
	}
	return used / float64(series[len(series)-1].TS-series[i].TS)
}

// ahead is a window's guess at t after now, from its last point: used at pace until its reset, then from nothing again each period.
func ahead(last windowPoint, pace float64, now, t, period int64) float64 {
	reset := last.Reset
	if reset == 0 {
		reset = now + period // no window open: the next one opens with the next use
	}
	used := last.Used + pace*float64(t-now)
	if t >= reset {
		used = pace * float64((t-reset)%period)
	}
	return 100 - min(100, used)
}

// aheadSplit is ahead with one pace until the window's reset and another after it.
func aheadSplit(last windowPoint, before, after float64, now, t, period int64) float64 {
	if last.Reset != 0 && t >= last.Reset {
		return ahead(last, after, now, t, period)
	}
	return ahead(last, before, now, t, period)
}

// capWeek is the week's use per second held to the pace line: until its reset, no more than what reaches Target at its end (the
// reset, or end when that comes first); in the weeks after, no more than Target spread over the week.
func (p *Paced) capWeek(last windowPoint, used float64, now int64, end float64) (before, after float64) {
	before, after = used, min(used, p.Target/float64(WeekS))
	stop := float64(last.Reset)
	if end != 0 && (stop == 0 || end < stop) {
		stop = end
	}
	if stop > float64(now) {
		before = min(used, max(0, p.Target-last.Used)/(stop-float64(now)))
	}
	return before, after
}

// resetsAhead are a window's resets from after now to to: the one its last point names and, while it is used, one each period after.
func resetsAhead(last windowPoint, pace float64, now, to, period int64, window string) []HeadroomReset {
	var out []HeadroomReset
	for r := last.Reset; r != 0 && r <= to; r += period {
		if r > now {
			out = append(out, HeadroomReset{r, window})
		}
		if pace == 0 {
			break
		}
	}
	return out
}

func leftOf(v float64) *float64 { return &v }

// HeadroomOver is the headroom from from to to every step seconds; a point after now is a guess. rows are usage_samples rows, oldest first,
// reaching back a day before now for the pace. ends is when an account that ends stops: looking back it has no reading once ended,
// looking ahead nothing left. An account whose last reading is stale has no guess, and the average ahead leaves it out.
func HeadroomOver(rows []sampleRow, now, from, to, step int64, ends map[string]float64) HeadroomSpan {
	return headroomOver(rows, now, from, to, step, ends, nil)
}

// Paced is how stats.outlook's by_account guesses: each window at its use over the last day, and an account whose last reading is stale
// keeps it until its reset. With On, an account's week goes at no more than its pace line's rate (to Target at the end of its week)
// and its 5h window at no more than that over Ratio, its measured weekly-to-5h ratio, and each point has its Line.
type Paced struct {
	On     bool
	Target float64
	Ratio  func(acct string) (float64, bool)
}

// HeadroomPaced is HeadroomOver as Paced guesses it, with each point's pace line.
func HeadroomPaced(rows []sampleRow, now, from, to, step int64, ends map[string]float64, p Paced) HeadroomSpan {
	return headroomOver(rows, now, from, to, step, ends, &p)
}

// lineLeft is what the pace line leaves of the week at t, for the week that resets at reset and ends at end (0: the reset); nil with
// pacing off.
func (p *Paced) lineLeft(t, reset int64, end float64) *float64 {
	if !p.On || reset == 0 {
		return nil
	}
	start, stop := float64(reset-WeekS), float64(reset)
	if end != 0 && end < stop {
		stop = end
	}
	if stop <= start {
		return nil
	}
	return leftOf(100 - p.Target*min(1, max(0, (float64(t)-start)/(stop-start))))
}

func headroomOver(rows []sampleRow, now, from, to, step int64, ends map[string]float64, paced *Paced) HeadroomSpan {
	fives, weeks := map[string][]windowPoint{}, map[string][]windowPoint{}
	for _, r := range rows {
		a, _ := r["account"].(string)
		five, week, hasFive, hasWeek := windowsOf(r)
		if hasFive && hasWeek {
			fives[a] = append(fives[a], five)
			weeks[a] = append(weeks[a], week)
		}
	}
	accounts := make([]string, 0, len(fives))
	for a := range fives {
		accounts = append(accounts, a)
	}
	sort.Strings(accounts)
	out := HeadroomSpan{}
	for _, a := range accounts {
		f, w := fives[a], weeks[a]
		end, ended := ends[a]
		h := AccountHeadroom{Account: a}
		seen := map[HeadroomReset]bool{}
		for i := range f {
			for _, r := range []HeadroomReset{{f[i].Reset, "5h"}, {w[i].Reset, "7d"}} {
				if r.At != 0 && r.At >= from && r.At <= min(now, to) && !seen[r] {
					seen[r] = true
					h.Resets = append(h.Resets, r)
				}
			}
		}
		lastF, lastW := f[len(f)-1], w[len(w)-1]
		fresh := now-lastF.TS <= OutlookMaxAgeS && !(ended && float64(now) >= end)
		paceF, paceW := usedPerS(f, now-headroomPaceS), pace(toWeekPoints(w))
		weekEnd := 0.0
		if ended {
			weekEnd = end
		}
		paceWAfter := paceW
		if paced != nil {
			fresh = !(ended && float64(now) >= end)
			paceW = usedPerS(w, now-headroomPaceS)
			paceWAfter = paceW
			if paced.On {
				paceW, paceWAfter = paced.capWeek(lastW, paceW, now, weekEnd)
				if r, ok := paced.Ratio(a); ok && r > 0 {
					paceF = min(paceF, paceW/r)
				}
			}
		}
		for t := from; t <= to; t += step {
			p := HeadroomPoint{At: t}
			gone := ended && float64(t) >= end
			switch {
			case t <= now:
				if fp, ok := windowAt(f, t); ok && !gone {
					wp, _ := windowAt(w, t)
					p.Five, p.Week = leftOf(100-fp.Used), leftOf(100-wp.Used)
					if paced != nil {
						p.Line = paced.lineLeft(t, wp.Reset, weekEnd)
					}
				}
				h.Past = append(h.Past, p)
				continue
			case !fresh:
			case gone:
				p.Five, p.Week = leftOf(0), leftOf(0)
			default:
				p.Five, p.Week = leftOf(ahead(lastF, paceF, now, t, fiveS)), leftOf(aheadSplit(lastW, paceW, paceWAfter, now, t, WeekS))
				if paced != nil {
					reset := lastW.Reset
					for reset != 0 && t >= reset {
						reset += WeekS
					}
					p.Line = paced.lineLeft(t, reset, weekEnd)
				}
			}
			h.Future = append(h.Future, p)
		}
		if fresh {
			until := to
			if ended {
				until = min(to, int64(end))
			}
			h.Resets = append(h.Resets, resetsAhead(lastF, paceF, now, until, fiveS, "5h")...)
			h.Resets = append(h.Resets, resetsAhead(lastW, paceW, now, until, WeekS, "7d")...)
		}
		sort.SliceStable(h.Resets, func(i, j int) bool { return h.Resets[i].At < h.Resets[j].At })
		out.Accounts = append(out.Accounts, h)
	}
	for i, t := 0, from; t <= to; i, t = i+1, t+step {
		p := averaged(out.Accounts, i, t)
		if t <= now {
			out.Past = append(out.Past, p)
		} else {
			out.Future = append(out.Future, p)
		}
	}
	return out
}

// toWeekPoints is the weekly points as pace reads them, so the weekly guess goes as stats.outlook's does.
func toWeekPoints(series []windowPoint) []weekPoint {
	out := make([]weekPoint, len(series))
	for i, s := range series {
		reset := s.Reset
		out[i] = weekPoint{s.TS, s.Used, &reset}
	}
	return out
}

// averaged is the mean of the accounts' binding headroom at the i-th point, t, over those that have one.
func averaged(accounts []AccountHeadroom, i int, t int64) OutlookPoint {
	out := OutlookPoint{At: t}
	var sum float64
	n := 0
	for _, h := range accounts {
		p := HeadroomPoint{}
		if i < len(h.Past) {
			p = h.Past[i]
		} else {
			p = h.Future[i-len(h.Past)]
		}
		if l := p.Left(); l != nil {
			sum += *l
			n++
		}
	}
	if n > 0 {
		out.Left = leftOf(sum / float64(n))
	}
	return out
}
