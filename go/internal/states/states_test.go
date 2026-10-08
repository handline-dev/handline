package states

import (
	"database/sql"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
)

func TestBackoffDoublesFromAMinuteAndStopsAtAnHour(t *testing.T) {
	for streak, want := range map[int]time.Duration{-1: 0, 0: 0, 1: time.Minute, 2: 2 * time.Minute, 3: 4 * time.Minute, 7: time.Hour, 80: time.Hour} {
		if got := BackoffFor(streak); got != want {
			t.Errorf("BackoffFor(%d) = %v, want %v", streak, got, want)
		}
	}
}

func TestParkWakeableSaysWhyNot(t *testing.T) {
	row := func(detail string, wake int64) *db.AgentState {
		r := &db.AgentState{Agent: "a", Detail: sql.NullString{String: detail, Valid: true}}
		r.WakeAt = sql.NullInt64{Int64: wake, Valid: wake != 0}
		return r
	}
	if ok, _ := ParkWakeable(row("by hand: lunch", 0)); !ok {
		t.Fatal("a park by hand is worth forcing")
	}
	if ok, why := ParkWakeable(row("moving to b", 0)); ok || !strings.Contains(why, "moving accounts") {
		t.Fatalf("%v %q", ok, why)
	}
	if ok, why := ParkWakeable(row("usage", 0)); ok || !strings.Contains(why, "starts ? when") {
		t.Fatalf("%v %q", ok, why)
	}
	at := time.Date(2026, 1, 2, 13, 5, 0, 0, time.Local).Unix()
	if _, why := ParkWakeable(row("usage", at)); !strings.Contains(why, "starts 13:05 when") {
		t.Fatal(why)
	}
}

func TestEveryWritableStateHasAWaker(t *testing.T) {
	for _, s := range []string{Idle, Queued, Running, Handoff, Degraded, Parked, Throttled, Stalled} {
		if Waker[s] == "" {
			t.Errorf("%s has no way back", s)
		}
	}
}
