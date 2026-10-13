package reads

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/usage"
)

func TestOutlookStatesTheRunwayTheCostAndKeepsTheFutureApart(t *testing.T) {
	e := testEnv(t)
	clock := time.Date(2026, 10, 8, 15, 0, 0, 0, time.Local)
	now := clock.Unix()
	for acct, pct := range map[string]int{"full": 100, "quiet": 20} {
		p := paths.StatusFile(e.Home, acct)
		os.MkdirAll(filepath.Dir(p), 0o755)
		body := fmt.Sprintf(`{"collected_at": %d, "windows": [{"minutes": 300, "used_percent": %d, "resets_at": %d, "observed_at": %d, "carried": false, "lost": false},`+
			`{"minutes": 10080, "used_percent": 40, "resets_at": %d, "observed_at": %d, "carried": false, "lost": false}]}`,
			now, pct, now+3600, now, now+86400, now)
		os.WriteFile(p, []byte(body), 0o644)
		for i := 0; i < 3; i++ {
			ts := now - int64(3-i)*1800
			w := fmt.Sprintf(`[{"minutes":300,"used_percent":5,"resets_at":%d},{"minutes":10080,"used_percent":%d,"resets_at":%d}]`, ts+3600, 30+i*5, now+86400)
			if _, err := e.DB.Exec("INSERT INTO usage_samples(account, ts, exists_, windows) VALUES(?,?,1,?)", acct, ts, w); err != nil {
				t.Fatal(err)
			}
		}
	}
	e.DB.Exec(`INSERT INTO token_calls(account, session, req, ts, model, cwd, side, inp, cc, cr, out, thinking)
		VALUES('full','s','r',?, 'claude-sonnet-5', '/nowhere', 0, 1234567, 0, 0, 0, 0)`, now-60)

	got, err := e.outlookAt("24h", 24, clock, nil)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := json.Marshal(got)
	var out struct {
		Range, Now string
		Accounts   int
		Past       []struct {
			At       string
			Left_pct *float64
		}
		Future []struct {
			At       string
			Left_pct *float64
		}
		Runway []struct {
			Account        string
			Runs_out       *int64 `json:"runs_out_in_seconds"`
			Week_resets_at string
		}
		Cost []struct {
			Day    string
			Agents map[string]float64
		} `json:"cost_per_day"`
	}
	if err := json.Unmarshal(b, &out); err != nil {
		t.Fatal(err)
	}
	if out.Range != "24h" || out.Now != "2026-10-08T"+clock.UTC().Format("15:04:05")+"Z" || out.Accounts != 2 || len(out.Past) == 0 || len(out.Future) == 0 {
		t.Fatalf("%s", b)
	}
	if len(out.Runway) != 2 || out.Runway[0].Account != "full" || out.Runway[0].Runs_out == nil || *out.Runway[0].Runs_out != 0 ||
		out.Runway[1].Runs_out != nil || out.Runway[0].Week_resets_at != time.Unix(now+86400, 0).UTC().Format("2006-01-02T15:04:05Z") {
		t.Fatalf("an account at the wall runs out now, one with no burn does not: %s", b)
	}
	if len(out.Cost) != 1 || out.Cost[0].Day != "2026-10-08" || out.Cost[0].Agents["(unattributed)"] != 2.4691 {
		t.Fatalf("1.234567 M sonnet-5 input tokens at $2, rounded to four places: %s", b)
	}
}

func TestOutlookRefusesARangeItDoesNotList(t *testing.T) {
	e := testEnv(t)
	if _, err := e.statsOutlook(nil, map[string]any{"range": "1y"}); err == nil {
		t.Fatal("bad_request expected")
	}
	if _, err := e.statsOutlook(nil, map[string]any{}); err == nil {
		t.Fatal("a missing range is refused")
	}
}

func TestAccountsListShowsTheCapShrunkByTheCutoffRiskNotThePace(t *testing.T) {
	e := testEnv(t)
	now := time.Now().Unix()
	for acct, used := range map[string]int{"fast": 95, "hot": 5} {
		p := paths.StatusFile(e.Home, acct)
		os.MkdirAll(filepath.Dir(p), 0o755)
		body := fmt.Sprintf(`{"collected_at": %d, "windows": [{"minutes": 300, "used_percent": 1, "resets_at": %d, "observed_at": %d, "carried": false, "lost": false},`+
			`{"minutes": 10080, "used_percent": %d, "resets_at": %d, "observed_at": %d, "carried": false, "lost": false}]}`,
			now, now+3600, now, used, now+6*86400, now)
		os.WriteFile(p, []byte(body), 0o644)
	}
	// 99 points left of the 5h window, an hour to its reset: 10%/min runs out in under ten minutes.
	if err := usage.WriteBurn(e.Home, "hot", 10.0/60, now); err != nil {
		t.Fatal(err)
	}
	got, err := e.accountsList(nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := json.Marshal(got)
	var out struct {
		Accounts []struct {
			Name          string
			Cap_effective int
		}
	}
	json.Unmarshal(b, &out)
	if len(out.Accounts) != 2 || out.Accounts[0].Name != "fast" || out.Accounts[0].Cap_effective != 2 || out.Accounts[1].Cap_effective != 1 {
		t.Fatalf("an account ahead of its week keeps the configured cap; one that would be cut off before its reset does not: %s", b)
	}
}

// outlookWithUse seeds two accounts whose week rose by 20 and 10 points, and two agents of the group grp who spent on them: grp.one $6 of
// a's $8 and $2 of b's $2, grp.two $2 of a's $8.
func outlookWithUse(t *testing.T) (*Env, time.Time) {
	t.Helper()
	e := testEnv(t)
	clock := time.Date(2026, 10, 8, 15, 0, 0, 0, time.Local)
	now := clock.Unix()
	for acct, rise := range map[string]int{"a": 20, "b": 10} {
		for i := 0; i < 3; i++ {
			ts := now - int64(3-i)*1800
			w := fmt.Sprintf(`[{"minutes":10080,"used_percent":%d,"resets_at":%d}]`, 10+rise*i/2, now+86400)
			run(t, e, "INSERT INTO usage_samples(account, ts, exists_, windows) VALUES(?,?,1,?)", acct, ts, w)
		}
	}
	run(t, e, "INSERT INTO agent_state(agent, state, updated, worktree) VALUES('grp.one', 'IDLE', 0, '/w/one'), ('grp.two', 'IDLE', 0, '/w/two')")
	call := func(acct, cwd string, tokens int) {
		run(t, e, `INSERT INTO token_calls(account, session, req, ts, model, cwd, side, inp, cc, cr, out, thinking)
			VALUES(?, 's', ?, ?, 'claude-sonnet-5', ?, 0, ?, 0, 0, 0, 0)`, acct, acct+cwd, now-60, cwd, tokens)
	}
	call("a", "/w/one", 3_000_000)
	call("a", "/w/two", 1_000_000)
	call("b", "/w/one", 1_000_000)
	return e, clock
}

func TestOutlookGivesWhatEachAgentUsedAndTheEstimateOfTheWeek(t *testing.T) {
	e, clock := outlookWithUse(t)
	got, err := e.outlookAt("24h", 24, clock, nil)
	if err != nil {
		t.Fatal(err)
	}
	res := got.(map[string]any)
	if _, has := res["overlay"]; has {
		t.Fatal("no agent asked for, no overlay")
	}
	uses := res["agent_use"].([]any)
	one, two := uses[0].(map[string]any), uses[1].(map[string]any)
	fr := one["fracs"].(map[string]any)
	if len(uses) != 2 || one["agent"] != "grp.one" || two["agent"] != "grp.two" || one["tokens"] != int64(4_000_000) ||
		one["cost"] != 8.0 || one["share"] != 0.8 || two["share"] != 0.2 || fr["a"] != 0.75 || fr["b"] != 1.0 {
		t.Fatalf("%v", uses)
	}
	// grp.one: 3/4 of a's 20 points and all of b's 10, over two accounts; grp.two: 1/4 of a's.
	if one["est_pct"] != 12.5 || two["est_pct"] != 2.5 {
		t.Fatalf("%v %v", one["est_pct"], two["est_pct"])
	}
}

func TestOutlookOverlayIsThePicksCurveAgainstPast(t *testing.T) {
	e, clock := outlookWithUse(t)
	for pick, nonEmpty := range map[string]bool{"grp.one": true, "grp:grp": true, "grp.nobody": false, "nobody": false} {
		got, err := e.outlookAt("24h", 24, clock, &pick)
		if err != nil {
			t.Fatal(err)
		}
		res := got.(map[string]any)
		overlay := res["overlay"].([]any)
		if nonEmpty != (len(overlay) > 0) || (nonEmpty && len(overlay) != len(res["past"].([]any))) {
			t.Fatalf("%s: %d overlay entries for %d past", pick, len(overlay), len(res["past"].([]any)))
		}
		for i := 1; i < len(overlay); i++ {
			if overlay[i].(float64) < overlay[i-1].(float64) {
				t.Fatalf("%s: the points used only grow: %v", pick, overlay)
			}
		}
		if nonEmpty && overlay[len(overlay)-1].(float64) <= 0 {
			t.Fatalf("%s: %v", pick, overlay)
		}
	}
}

// The overlay by number. Past is a half-hour grid from now-24h to now (49 points) and the last three samples of each account fall on
// 04:30, 05:00 and 05:30 UTC, the 46th to 48th points of it. Account a (used 10, 20, 30) also has two samples before past begins (2 and
// 4); account b (10, 15, 20) none. The curve of an account is the points it used from past[0] on, a rise into a sample counting only
// when the sample is inside past:
//
//	a: 0 for the 45 points before 04:30, then 6 (4 -> 10 into the first sample), 16, 26, 26   (the 2 -> 4 before past is not counted)
//	b: 0 for the 45 points before 04:30, then 0 (nothing before it), 5, 10, 10
//
// grp.one spent 3/4 of a's cost and all of b's; the group grp all of both. Averaged over the two accounts:
//
//	grp.one: (3/4*a + b) / 2 = 2.25, 8.5, 14.75, 14.75      grp:grp: (a + b) / 2 = 3, 10.5, 18, 18
func TestOutlookOverlayValuesByNumber(t *testing.T) {
	e, clock := outlookWithUse(t)
	now := clock.Unix()
	for _, s := range []struct {
		ago  int64
		used int
	}{{25*3600 + 1800, 2}, {25 * 3600, 4}} {
		w := fmt.Sprintf(`[{"minutes":10080,"used_percent":%d,"resets_at":%d}]`, s.used, now+86400)
		run(t, e, "INSERT INTO usage_samples(account, ts, exists_, windows) VALUES('a',?,1,?)", now-s.ago, w)
	}
	for pick, tail := range map[string][4]float64{"grp.one": {2.25, 8.5, 14.75, 14.75}, "grp:grp": {3, 10.5, 18, 18}} {
		got, err := e.outlookAt("24h", 24, clock, &pick)
		if err != nil {
			t.Fatal(err)
		}
		res := got.(map[string]any)
		past, overlay := res["past"].([]any), res["overlay"].([]any)
		if len(past) != 49 || past[0].(map[string]any)["at"] != ts(now-24*3600) {
			t.Fatalf("the grid this test counts on: %d points from %v", len(past), past[0])
		}
		want := make([]any, 45, 49)
		for i := range want {
			want[i] = 0.0
		}
		for _, v := range tail {
			want = append(want, v)
		}
		if fmt.Sprint(overlay) != fmt.Sprint(want) {
			t.Fatalf("%s:\n got %v\nwant %v", pick, overlay, want)
		}
	}
}
