package reads

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/paths"
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

	got, err := e.outlookAt("24h", 24, clock)
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
