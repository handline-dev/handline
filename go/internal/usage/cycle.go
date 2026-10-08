package usage

import "time"

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
	start, ok := Started(home, acct)
	if !ok {
		return ""
	}
	return anniversary(start, now).Format("2006-01-02")
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
	start, ok := Started(home, acct)
	if !ok {
		return time.Time{}, false
	}
	return anniversary(start, now), true
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
