package reads

import (
	"fmt"
	"strconv"
	"strings"
	"time"
)

// cronExpr is a five-field cron expression in local time, as handline/infra/cron.py reads it: `*`, `*/n`, `a`, `a-b`, `a-b/n` and
// comma lists. Day-of-week is 0-6 with Sunday 0 (7 is Sunday too); when both day fields are restricted a day matches if either does.
type cronExpr struct {
	fields         [5]map[int]bool
	domAny, dowAny bool
}

var cronRanges = [5][2]int{{0, 59}, {0, 23}, {1, 31}, {1, 12}, {0, 7}}
var cronNames = [5]string{"minute", "hour", "day of month", "month", "day of week"}

// cronHorizon is the furthest nextAfter looks: an expression that matches nothing in this long has no next time.
const cronHorizon = 366 * 8

func cronField(text string, lo, hi int, name string) (map[int]bool, error) {
	out := map[int]bool{}
	for _, part := range strings.Split(text, ",") {
		step := 1
		body, rest, slash := strings.Cut(part, "/")
		if slash {
			n, _ := strconv.Atoi(rest)
			if !isDigits(rest) || n < 1 {
				return nil, fmt.Errorf("%s: a step is a positive number, not %q", name, rest)
			}
			step = n
		}
		var a, b int
		switch {
		case body == "*":
			a, b = lo, hi
		case strings.Contains(body, "-"):
			x, y, _ := strings.Cut(body, "-")
			if !isDigits(x) || !isDigits(y) {
				return nil, fmt.Errorf("%s: %q is not a range", name, part)
			}
			a, _ = strconv.Atoi(x)
			b, _ = strconv.Atoi(y)
		case isDigits(body):
			a, _ = strconv.Atoi(body)
			b = a
			if slash {
				b = hi
			}
		default:
			return nil, fmt.Errorf("%s: %q is not a number, a range or *", name, part)
		}
		if a < lo || b > hi || a > b {
			return nil, fmt.Errorf("%s: %q is outside %d-%d", name, part, lo, hi)
		}
		for i := a; i <= b; i += step {
			out[i] = true
		}
	}
	return out, nil
}

func parseCron(expr string) (*cronExpr, error) {
	parts := strings.Fields(expr)
	if len(parts) != 5 {
		return nil, fmt.Errorf("a cron expression has five fields: minute hour day-of-month month day-of-week")
	}
	c := &cronExpr{domAny: strings.HasPrefix(parts[2], "*"), dowAny: strings.HasPrefix(parts[4], "*")}
	for i, p := range parts {
		f, err := cronField(p, cronRanges[i][0], cronRanges[i][1], cronNames[i])
		if err != nil {
			return nil, err
		}
		c.fields[i] = f
	}
	dow := map[int]bool{}
	for d := range c.fields[4] {
		dow[d%7] = true
	}
	c.fields[4] = dow
	return c, nil
}

func (c *cronExpr) dayOK(t time.Time) bool {
	dom := c.fields[2][t.Day()]
	dow := c.fields[4][int(t.Weekday())]
	if c.domAny || c.dowAny {
		return dom && dow
	}
	return dom || dow
}

// nextAfter is the first minute after ts the expression names, as a timestamp, or false within eight years. Stepping is by wall-clock
// fields, as Python's naive datetimes do.
func (c *cronExpr) nextAfter(ts int64) (int64, bool) {
	at := func(y int, m time.Month, d, h, mi int) time.Time { return time.Date(y, m, d, h, mi, 0, 0, time.Local) }
	t := time.Unix(ts, 0).In(time.Local)
	t = at(t.Year(), t.Month(), t.Day(), t.Hour(), t.Minute()+1)
	end := t.AddDate(0, 0, cronHorizon)
	for t.Before(end) {
		switch {
		case !c.fields[3][int(t.Month())]:
			t = at(t.Year(), t.Month()+1, 1, 0, 0)
		case !c.dayOK(t):
			t = at(t.Year(), t.Month(), t.Day()+1, 0, 0)
		case !c.fields[1][t.Hour()]:
			t = at(t.Year(), t.Month(), t.Day(), t.Hour()+1, 0)
		case !c.fields[0][t.Minute()]:
			t = at(t.Year(), t.Month(), t.Day(), t.Hour(), t.Minute()+1)
		default:
			return t.Unix(), true
		}
	}
	return 0, false
}
