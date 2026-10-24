package usage

import "time"

// CycleOn is cycle.enabled.
func (c Config) CycleOn(acct string) bool { return c["month_cycle_"+acct] == true }

// EndIsDefault is cycle.end_is_default: no end date was typed.
func (c Config) EndIsDefault(acct string) bool { return c["sub_end_"+acct] == nil }

// EndDate is cycle.end_date: the last day the account can be used as YYYY-MM-DD, or "" when none was typed. A month cycle with no typed
// day has no end: the subscription may have been renewed, and the account counts as ended only once its login drops.
func (c Config) EndDate(home, acct string, now time.Time) string {
	v := c["sub_end_"+acct]
	if c["month_cycle_"+acct] != true {
		if _, written := c["month_cycle_"+acct]; v == nil || written {
			return ""
		}
	}
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

// EndOf is cycle.end_of: when the account stops being usable; ok is false when it is not on a cycle or no end day was typed. A typed
// end day is usable through all of it, so the moment is the start of the next.
func (c Config) EndOf(home, acct string, now time.Time) (time.Time, bool) {
	day := c.EndDate(home, acct, now)
	if day == "" {
		return time.Time{}, false
	}
	return DayEnd(day)
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
