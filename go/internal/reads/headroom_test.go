package reads

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
)

// "full" spends its last 5h points before its reset: the average reaches nothing when both are out, and the first reset ahead is named.
func TestHeadroomGivesEachAccountsWindowsTheFirstResetAndWhenItRunsOut(t *testing.T) {
	e := testEnv(t)
	clock := time.Date(2026, 10, 8, 15, 0, 0, 0, time.UTC)
	now := clock.Unix()
	for acct, start := range map[string]int{"full": 70, "also": 70} {
		dir := filepath.Join(e.Home, "accounts", acct) // logged in: an account that is not counts as gone
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		os.WriteFile(filepath.Join(dir, ".credentials.json"), []byte(`{}`), 0o600)
		for i := 0; i <= 2; i++ {
			ts := now - int64(2-i)*1800
			w := fmt.Sprintf(`[{"minutes":300,"used_percent":%d,"resets_at":%d},{"minutes":10080,"used_percent":10,"resets_at":%d}]`,
				start+i*10, now+4*3600, now+86400)
			if _, err := e.DB.Exec("INSERT INTO usage_samples(account, ts, exists_, windows) VALUES(?,?,1,?)", acct, ts, w); err != nil {
				t.Fatal(err)
			}
		}
	}
	got, err := e.headroomAt(clock, now-3600, now+4*3600, 1800)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := json.Marshal(got)
	var out struct {
		Now      string
		StepS    int64 `json:"step_s"`
		Combined struct {
			Past, Future []struct {
				At   string
				Left *float64 `json:"left_pct"`
			}
		}
		Accounts []struct {
			Account      string
			Past, Future []struct {
				At       string
				FiveLeft *float64 `json:"five_left"`
				WeekLeft *float64 `json:"week_left"`
			}
			Resets []struct{ At, Window string }
		}
		RunsOutAt *string                               `json:"runs_out_at"`
		NextReset *struct{ At, Account, Window string } `json:"next_reset"`
	}
	if err := json.Unmarshal(b, &out); err != nil {
		t.Fatal(err)
	}
	if out.Now != "2026-10-08T15:00:00Z" || out.StepS != 1800 || len(out.Combined.Past) != 3 || len(out.Combined.Future) != 8 {
		t.Fatalf("%s", b)
	}
	if len(out.Accounts) != 2 || out.Accounts[0].Account != "also" {
		t.Fatalf("accounts by name: %s", b)
	}
	// 20 points an hour from 90 used: 10 left now, out in half an hour, until the reset in 4 hours.
	a := out.Accounts[1]
	if p := a.Past[2]; p.FiveLeft == nil || *p.FiveLeft != 10 || *p.WeekLeft != 90 {
		t.Errorf("full now: %s", b)
	}
	if out.RunsOutAt == nil || *out.RunsOutAt != "2026-10-08T15:30:00Z" {
		t.Errorf("runs out: %v", out.RunsOutAt)
	}
	if out.NextReset == nil || out.NextReset.At != "2026-10-08T19:00:00Z" || out.NextReset.Window != "5h" {
		t.Errorf("next reset: %+v", out.NextReset)
	}
	if len(a.Resets) != 1 || a.Resets[0].At != "2026-10-08T19:00:00Z" {
		t.Errorf("resets: %+v", a.Resets)
	}
}

func TestHeadroomRefusesASpanItCannotDraw(t *testing.T) {
	e := testEnv(t)
	for _, c := range []struct {
		params      map[string]any
		field, says string
	}{
		{map[string]any{"from": "2026-01-02T00:00:00Z", "to": "2026-01-01T00:00:00Z"}, "to", "after"},
		{map[string]any{"step_s": json.Number("59")}, "step_s", "at least 60"},
		{map[string]any{"step_s": "15m"}, "step_s", "integer"},
		{map[string]any{"from": "yesterday"}, "from", "RFC 3339"},
		{map[string]any{"to": json.Number("5")}, "to", "string"},
		{map[string]any{"from": "2026-01-01T00:00:00Z", "to": "2026-01-02T09:20:00Z", "step_s": json.Number("60")}, "step_s", "2000"}, // 2001 points
	} {
		_, err := e.statsHeadroom(nil, c.params)
		var re *rpc.Error
		if !errors.As(err, &re) || re.Data["field"] != c.field || !strings.Contains(re.Message, c.says) {
			t.Errorf("%v: %v", c.params, err)
		}
	}
	if _, err := e.statsHeadroom(nil, map[string]any{"from": "2026-01-01T00:00:00Z", "to": "2026-01-02T09:19:00Z", "step_s": json.Number("60")}); err != nil {
		t.Errorf("2000 points: %v", err)
	}
	got, err := e.statsHeadroom(nil, map[string]any{})
	if err != nil {
		t.Fatal(err)
	}
	if m := got.(map[string]any); m["step_s"] != int64(900) {
		t.Errorf("default step: %v", m["step_s"])
	}
}

func TestHeadroomForecastsWhatTheWeekLeavesAtItsResetAndNullWhereItCannotRead(t *testing.T) {
	e := testEnv(t)
	clock := time.Date(2026, 10, 8, 15, 0, 0, 0, time.UTC)
	now := clock.Unix()
	week := func(used int) string {
		return fmt.Sprintf(`[{"minutes":300,"used_percent":5,"resets_at":%d},{"minutes":10080,"used_percent":%d,"resets_at":%d}]`, now+3600, used, now+50*3600)
	}
	// a read now at 50% used with the week resetting in 50 h; it was 26% a day ago: 24 points a day, 1 a hour, 50 more by the reset.
	for _, r := range []struct {
		ts   int64
		used int
	}{{now - 23*3600, 26}, {now - 12*3600, 38}, {now - 60, 50}} {
		if _, err := e.DB.Exec("INSERT INTO usage_samples(account, ts, exists_, windows) VALUES('a',?,1,?)", r.ts, week(r.used)); err != nil {
			t.Fatal(err)
		}
	}
	p := paths.StatusFile(e.Home, "a")
	os.MkdirAll(filepath.Dir(p), 0o755)
	body := fmt.Sprintf(`{"collected_at": %d, "windows": [{"minutes": 300, "used_percent": 5, "resets_at": %d, "observed_at": %d},`+
		`{"minutes": 10080, "used_percent": 50, "resets_at": %d, "observed_at": %d}]}`, now, now+3600, now, now+50*3600, now)
	os.WriteFile(p, []byte(body), 0o644)
	got, err := e.headroomAt(clock, now-3600, now+3600, 1800)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := json.Marshal(got)
	var out struct {
		Accounts []struct {
			Account    string
			LeftAtRest *float64 `json:"left_at_reset"`
			LostAnyway *float64 `json:"lost_anyway"`
		}
	}
	json.Unmarshal(b, &out)
	if len(out.Accounts) != 1 || out.Accounts[0].LeftAtRest == nil || *out.Accounts[0].LeftAtRest != 0 || out.Accounts[0].LostAnyway != nil {
		t.Fatalf("50 unused, 24 points a day for 50 h uses it all (0 left); no ratio, no lost_anyway: %s", b)
	}
}
