package usage

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
)

// coldEnv has an account whose status file reads `pct` percent of the shortest window, fresh at the clock.
func coldEnv(t *testing.T, pct int) (*Env, db.Q, *int64) {
	e, q, now := burnEnv(t)
	writeColdStatus(t, e, "a", pct)
	return e, q, now
}

func writeColdStatus(t *testing.T, e *Env, acct string, pct int) {
	t.Helper()
	p := paths.StatusFile(e.Home, acct)
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	body := `{"collected_at": ` + itoa(int(e.now())) + `, "windows": [{"minutes": 300, "used_percent": ` + itoa(pct) +
		`, "resets_at": ` + itoa(int(e.now())+9000) + `, "observed_at": ` + itoa(int(e.now())) + `, "carried": false, "lost": false}]}`
	if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func coldEvents(t *testing.T, q db.Q) []string {
	t.Helper()
	rows, err := q.Query("SELECT payload FROM events WHERE kind = 'cold_start' ORDER BY id")
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var p string
		rows.Scan(&p)
		out = append(out, p)
	}
	return out
}

func TestAColdStartIsMeasuredFromBeginToTheFirstStop(t *testing.T) {
	e, q, now := coldEnv(t, 20)
	if err := e.BeginColdStart(q, "x", "a"); err != nil {
		t.Fatal(err)
	}
	if err := e.SettleColdStart(q, "x"); err != nil || len(coldEvents(t, q)) != 0 {
		t.Fatalf("open until the first turn ends: %v", err)
	}
	*now += 300
	writeColdStatus(t, e, "a", 33)
	event(t, q, *now, "x", "Stop", "")
	if err := e.SettleColdStart(q, "x"); err != nil {
		t.Fatal(err)
	}
	got := coldEvents(t, q)
	if len(got) != 1 || got[0] != `{"account": "a", "before": 20, "after": 33, "pct": 13}` {
		t.Fatalf("%v", got)
	}
	if raw, _, _ := db.Meta(q, coldKey("x")); raw != "" {
		t.Fatal("a closed measurement is forgotten")
	}
}

func TestAColdStartIsNotMeasuredOnABusyAccountOrOnAFall(t *testing.T) {
	e, q, now := coldEnv(t, 20)
	setAgent(t, q, "y", "RUNNING", "a")
	e.BeginColdStart(q, "x", "a")
	if raw, _, _ := db.Meta(q, coldKey("x")); raw != "" {
		t.Fatal("another agent was spending the window at the start")
	}
	db.SetAgentState(q, "y", "IDLE", 0, "")
	e.BeginColdStart(q, "x", "a")
	*now += 10
	writeColdStatus(t, e, "a", 5)
	event(t, q, *now, "x", "Stop", "")
	e.SettleColdStart(q, "x")
	if len(coldEvents(t, q)) != 0 {
		t.Fatal("a window that fell measured nothing")
	}
	e.BeginColdStart(q, "x", "none")
	if raw, _, _ := db.Meta(q, coldKey("x")); raw != "" {
		t.Fatal("no reading, no measurement")
	}
}

func TestColdStartPctIsTheMedianOfTheRecentOnesOnceThereAreThree(t *testing.T) {
	_, q, _ := burnEnv(t)
	for i, pct := range []int{10, 14} {
		db.AddEventAt(q, int64(i+1), "x", "cold_start", db.KV{K: "pct", V: pct})
	}
	if _, ok, _ := ColdStartPct(q); ok {
		t.Fatal("two are too few")
	}
	db.AddEventAt(q, 3, "x", "cold_start", db.KV{K: "pct", V: 30})
	db.AddEventAt(q, 4, "x", "cold_start") // no payload: skipped
	if v, ok, err := ColdStartPct(q); err != nil || !ok || v != 14 {
		t.Fatalf("%v %v %v", v, ok, err)
	}
}
