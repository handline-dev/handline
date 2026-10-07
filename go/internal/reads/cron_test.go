package reads

import (
	"testing"
	"time"
)

func TestCronNextAfter(t *testing.T) {
	time.Local = time.UTC
	at := func(s string) int64 { v, _ := time.Parse("2006-01-02 15:04", s); return v.Unix() }
	for _, c := range []struct{ expr, from, want string }{
		{"*/15 9-17 * * 1-5", "2026-10-09 17:50", "2026-10-12 09:00"}, // Friday evening to Monday
		{"0 8 1 * *", "2026-10-01 08:00", "2026-11-01 08:00"},
		{"30 2 * * 7", "2026-10-08 00:00", "2026-10-11 02:30"}, // 7 is Sunday
		{"0 0 29 2 *", "2026-10-08 00:00", "2028-02-29 00:00"},
		{"0 0 1 1,6 1", "2026-10-08 00:00", "2027-01-01 00:00"}, // both day fields restricted: either matches
	} {
		x, err := parseCron(c.expr)
		if err != nil {
			t.Fatal(err)
		}
		got, ok := x.nextAfter(at(c.from))
		if !ok || got != at(c.want) {
			t.Errorf("%s after %s: got %v, want %s", c.expr, c.from, time.Unix(got, 0).UTC(), c.want)
		}
	}
	if x, _ := parseCron("0 0 31 2 *"); x != nil {
		if _, ok := x.nextAfter(0); ok {
			t.Error("Feb 31 should have no next time")
		}
	}
	for _, bad := range []string{"* * * *", "*/0 * * * *", "60 * * * *", "a * * * *", "5-1 * * * *"} {
		if _, err := parseCron(bad); err == nil {
			t.Errorf("%q should not parse", bad)
		}
	}
}

func TestScheduleID(t *testing.T) {
	s := schedule{"0 8 * * *", "hi"}
	if len(s.sid()) != 10 || scheduleID("a", s) == scheduleID("b", s) {
		t.Error("ids should be 10 hex digits and differ by agent")
	}
}
