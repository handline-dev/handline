// Package timefmt writes times and lengths of time one way everywhere (handline/infra/timefmt.py).
package timefmt

import (
	"fmt"
	"strconv"
	"strings"
	"time"
)

// Span is a length of time, every form ending in its unit: 6d18h from a day up, 5h03m from an hour, then minutes
// (precise adds the seconds: 12m05s).
func Span(secs int64, precise bool) string {
	switch {
	case secs >= 86400:
		return fmt.Sprintf("%dd%dh", secs/86400, secs%86400/3600)
	case secs >= 3600:
		return fmt.Sprintf("%dh%02dm", secs/3600, secs%3600/60)
	case precise:
		return fmt.Sprintf("%2dm%02ds", secs/60, secs%60)
	}
	return fmt.Sprintf("%dm", max(int64(1), secs/60))
}

// Ago is a duration in the largest unit that still says something.
func Ago(secs int64) string {
	switch {
	case secs < 120:
		return fmt.Sprintf("%ds", secs)
	case secs < 5400:
		return fmt.Sprintf("%dm", secs/60)
	case secs < 86400:
		return fmt.Sprintf("%dh", secs/3600)
	}
	return Span(secs, false)
}

var spanUnits = map[byte]int64{'s': 1, 'm': 60, 'h': 3600, 'd': 86400}

// ParseSpan is 90m, 2h, 45s or 1d as seconds. The unit is required, and the number is positive.
func ParseSpan(text string) (int64, error) {
	t := strings.ToLower(strings.TrimSpace(text))
	bad := fmt.Errorf("a positive number with a unit: 45s, 90m, 2h or 1d")
	if len(t) < 2 {
		return 0, bad
	}
	unit, ok := spanUnits[t[len(t)-1]]
	digits := t[:len(t)-1]
	if !ok || strings.Trim(digits, "0123456789") != "" {
		return 0, bad
	}
	n, err := strconv.ParseInt(digits, 10, 64)
	if err != nil || n == 0 {
		return 0, bad
	}
	return n * unit, nil
}

// AgoShort is how long since ts, in whole d, h or m.
func AgoShort(ts int64, now time.Time) string {
	secs := max(int64(0), now.Unix()-ts)
	switch {
	case secs >= 86400:
		return fmt.Sprintf("%dd", secs/86400)
	case secs >= 3600:
		return fmt.Sprintf("%dh", secs/3600)
	}
	return fmt.Sprintf("%dm", secs/60)
}

// Layouts for Stamp: the date and time, the month, day and time, and the clock (handline/infra/timefmt.py DATE_TIME, SHORT, CLOCK).
const (
	DateTime = "2006-01-02 15:04"
	Short    = "01-02 15:04"
	Clock    = "15:04"
)

// Stamp is ts in local time.
func Stamp(ts int64, layout string) string { return time.Unix(ts, 0).Local().Format(layout) }
