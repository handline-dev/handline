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
