package usage

import (
	"encoding/json"
	"os"
	"path/filepath"
	"time"

	"github.com/handline/handline/go/internal/paths"
)

// CycleOn is cycle.enabled.
func (c Config) CycleOn(acct string) bool { return c["month_cycle_"+acct] == true }

// EndIsDefault is cycle.end_is_default: no end date was typed.
func (c Config) EndIsDefault(acct string) bool { return c["sub_end_"+acct] == nil }

func addMonths(d time.Time, n int) time.Time {
	m0 := d.Year()*12 + int(d.Month()) - 1 + n
	y, m := m0/12, m0%12+1
	last := time.Date(y, time.Month(m)+1, 0, 0, 0, 0, 0, time.Local).Day()
	day := d.Day()
	if day > last {
		day = last
	}
	return time.Date(y, time.Month(m), day, 0, 0, 0, 0, time.Local)
}

// anniversary is the first monthly anniversary of start after now, at its time of day.
func anniversary(start time.Time, now time.Time) time.Time {
	sd := time.Date(start.Year(), start.Month(), start.Day(), 0, 0, 0, 0, time.Local)
	for n := 1; ; n++ {
		d := addMonths(sd, n)
		t := time.Date(d.Year(), d.Month(), d.Day(), start.Hour(), start.Minute(), start.Second(), start.Nanosecond(), time.Local)
		if t.After(now) {
			return t
		}
	}
}

// EndDate is cycle.end_date: the last day the account can be used as YYYY-MM-DD, or "" when it has none.
func (c Config) EndDate(home, acct string, now time.Time) string {
	v := c["sub_end_"+acct]
	if c["month_cycle_"+acct] != true {
		if _, written := c["month_cycle_"+acct]; v == nil || written {
			return ""
		}
	}
	if v != nil {
		s, ok := v.(string)
		if !ok {
			return ""
		}
		d, err := time.ParseInLocation("2006-01-02", s, time.Local)
		if err != nil {
			if d, err = time.ParseInLocation("20060102", s, time.Local); err != nil {
				return ""
			}
		}
		return d.Format("2006-01-02")
	}
	end, ok := defaultEnd(home, acct, now)
	if !ok {
		return ""
	}
	return end.Format("2006-01-02")
}

// pinFile holds a default end once the daemon has seen it: the subscription start it was worked out from and the moment it falls.
func pinFile(home, acct string) string {
	return filepath.Join(paths.AccountDir(home, acct), "cycle_end.json")
}

type pin struct {
	Start int64 `json:"start"`
	At    int64 `json:"at"`
}

// defaultEnd is the end of a cycle with no typed end day: the subscription's next anniversary, held where it was once pinned. A
// month-cycle subscription does not renew, so the anniversary ahead stays its end once reached instead of moving on a month. A new
// subscription start makes the pin stale.
func defaultEnd(home, acct string, now time.Time) (time.Time, bool) {
	start, ok := Started(home, acct)
	if !ok {
		return time.Time{}, false
	}
	var p pin
	if b, err := os.ReadFile(pinFile(home, acct)); err == nil && json.Unmarshal(b, &p) == nil && p.Start == start.Unix() && p.At != 0 {
		return time.Unix(p.At, 0).In(time.Local), true
	}
	return anniversary(start, now), true
}

// PinEnd keeps a month-cycle account's default end where it is now, and drops the pin once the cycle is off or an end day is typed.
func (c Config) PinEnd(home, acct string, now time.Time) error {
	f := pinFile(home, acct)
	if !c.CycleOn(acct) || !c.EndIsDefault(acct) {
		if err := os.Remove(f); err != nil && !os.IsNotExist(err) {
			return err
		}
		return nil
	}
	start, ok := Started(home, acct)
	if !ok {
		return nil
	}
	end, _ := defaultEnd(home, acct, now)
	b, _ := json.Marshal(pin{Start: start.Unix(), At: end.Unix()})
	if old, err := os.ReadFile(f); err == nil && string(old) == string(b) {
		return nil
	}
	return os.WriteFile(f, b, 0o644)
}

// DayEnd is the moment a last usable day (YYYY-MM-DD) is over: the start of the day after it, local time. ok is false for text
// that is not a date.
func DayEnd(day string) (time.Time, bool) {
	d, err := time.ParseInLocation("2006-01-02", day, time.Local)
	if err != nil {
		if d, err = time.ParseInLocation("20060102", day, time.Local); err != nil {
			return time.Time{}, false
		}
	}
	return time.Date(d.Year(), d.Month(), d.Day()+1, 0, 0, 0, 0, time.Local), true
}

// EndOf is cycle.end_of: when the account stops being usable; ok is false when it is not on a cycle or no end is known. A typed end
// day is usable through all of it, so the moment is the start of the next; a default end is the anniversary's own moment.
func (c Config) EndOf(home, acct string, now time.Time) (time.Time, bool) {
	day := c.EndDate(home, acct, now)
	if day == "" {
		return time.Time{}, false
	}
	if c["sub_end_"+acct] != nil {
		return DayEnd(day)
	}
	return defaultEnd(home, acct, now)
}

// DefaultWarnDays is how many days ahead the founder is told an account ends.
const DefaultWarnDays = 3

// WarnDays is cycle.warn_days: a whole number of days at or above zero, else the default.
func (c Config) WarnDays() int {
	switch n := c["cycle_warn_days"].(type) {
	case int:
		if n >= 0 {
			return n
		}
	case int64:
		if n >= 0 {
			return int(n)
		}
	}
	return DefaultWarnDays
}
