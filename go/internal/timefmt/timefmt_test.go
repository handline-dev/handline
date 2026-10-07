package timefmt

import (
	"testing"
	"time"
)

func TestSpanAndAgo(t *testing.T) {
	for secs, want := range map[int64][2]string{0: {"1m", "0s"}, 59: {"1m", "59s"}, 119: {"1m", "119s"}, 120: {"2m", "2m"}, 5399: {"1h29m", "89m"},
		5400: {"1h30m", "1h"}, 3600: {"1h00m", "60m"}, 86399: {"23h59m", "23h"}, 86400: {"1d0h", "1d0h"}, 162 * 3600: {"6d18h", "6d18h"}} {
		if Span(secs, false) != want[0] || Ago(secs) != want[1] {
			t.Errorf("%d: %s %s, want %v", secs, Span(secs, false), Ago(secs), want)
		}
	}
	if Span(725, true) != "12m05s" || Span(65, true) != " 1m05s" {
		t.Errorf("%q %q", Span(725, true), Span(65, true))
	}
}

func TestParseSpan(t *testing.T) {
	for in, want := range map[string]int64{"45s": 45, "90m": 5400, "2h": 7200, "1d": 86400, " 3H ": 10800} {
		if got, err := ParseSpan(in); err != nil || got != want {
			t.Errorf("%q: %d %v", in, got, err)
		}
	}
	for _, bad := range []string{"90", "m", "0m", "-1h", "1.5h", "2w", "", "1 h"} {
		if _, err := ParseSpan(bad); err == nil {
			t.Errorf("%q was taken", bad)
		}
	}
}

func TestAgoShort(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	for ago, want := range map[int64]string{30: "0m", 3000: "50m", 7300: "2h", 90000: "1d", -5: "0m"} {
		if got := AgoShort(now.Unix()-ago, now); got != want {
			t.Errorf("%d: %s", ago, got)
		}
	}
}
