package main

import (
	"testing"
	"time"

	"github.com/handline/handline/go/internal/handoff"
)

func TestTheSlowReportPutsWhatSeveralAgentsReportFirstThenEachAgentsNewestFirst(t *testing.T) {
	at := time.Date(2026, 10, 27, 10, 0, 0, 0, time.Local)
	got := slowReport([]handoff.Slowed{
		{Agent: "b", At: at, Line: "Slowed: check.sh ran twice (10 min)"},
		{Agent: "b", At: at, Line: "Slowed: gh pr checks timed out"},
		{Agent: "a", At: at.Add(-time.Hour), Line: "Slowed:  check.sh ran twice  (10 min)"},
	})
	want := "reported more than once:\n" +
		"  2x by 2 agent(s)  Slowed: check.sh ran twice (10 min)\n\n" +
		"b\n  10-27 10:00  Slowed: check.sh ran twice (10 min)\n  10-27 10:00  Slowed: gh pr checks timed out\n" +
		"a\n  10-27 09:00  Slowed:  check.sh ran twice  (10 min)\n"
	if got != want {
		t.Fatalf("got\n%s", got)
	}
}
