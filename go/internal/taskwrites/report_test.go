package taskwrites

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
)

// reviewTeam is the team reportEnv's home has: w works in lead's team, whose auditor is lead.auditor; boss and mgr are in none.
const reviewTeam = "teams:\n  lead: [lead, w, lead.auditor]\nagents:\n  lead: {}\n  w: {}\n  lead.auditor: {}\n  boss: {}\n  mgr: {}\n"

// reportEnv is a database with the agents w (who works the tasks), boss (who opens them), mgr, lead (w's lead) and lead.auditor, and a
// caller of any method as any of them.
func reportEnv(t *testing.T, rows ...string) (*sql.DB, *fakeTeller, func(agent, method, rid, params string) map[string]any) {
	t.Helper()
	return reportEnvIn(t, t.TempDir(), rows...)
}

// reportEnvIn is reportEnv with the daemon's home at home: it has reviewTeam unless it has a team.yaml already.
func reportEnvIn(t *testing.T, home string, rows ...string) (*sql.DB, *fakeTeller, func(agent, method, rid, params string) map[string]any) {
	t.Helper()
	sdb, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { sdb.Close() })
	if _, err := os.Stat(filepath.Join(home, "team.yaml")); os.IsNotExist(err) {
		if err := os.WriteFile(filepath.Join(home, "team.yaml"), []byte(reviewTeam), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	for _, a := range []string{"w", "boss", "mgr", "lead", "lead.auditor"} {
		os.MkdirAll(filepath.Join(home, "agents", a), 0o755)
		os.WriteFile(filepath.Join(home, "agents", a, "agent.yaml"), nil, 0o644)
	}
	for _, q := range rows {
		if _, err := sdb.Exec(q); err != nil {
			t.Fatalf("%s: %v", q, err)
		}
	}
	f := &fakeTeller{}
	return sdb, f, func(agent, method, rid, params string) map[string]any {
		s := rpc.NewServer(auth{agent}, "x", store.LatestVersion(), "local", func(string, ...any) {})
		s.DB = sdb
		Register(s, home, f, nil)
		return call(t, s, fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":%q,"params":{"request_id":%q%s}}`, method, rid, params))[0]
	}
}

func said(t *testing.T, r map[string]any) string {
	t.Helper()
	if r["error"] != nil {
		t.Fatalf("refused: %v", r["error"])
	}
	return r["result"].(map[string]any)["said"].(string)
}

func taskState(t *testing.T, sdb *sql.DB, tid int64) string {
	t.Helper()
	tx, err := sdb.Begin()
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	row, err := getTask(tx, tid)
	if err != nil {
		t.Fatal(err)
	}
	st, err := state(tx, row)
	if err != nil {
		t.Fatal(err)
	}
	return st
}

// A done closes the task, whoever it answers to and whatever lead its owner has: nothing waits on an accept.
func TestDoneClosesAtOnce(t *testing.T) {
	sdb, f, run := reportEnv(t,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, reporter) VALUES(1, 'own', 'w', 'w', 1, NULL), (2, 'f', 'w', 'boss', 1, NULL), "+
			"(3, 'rep', 'w', 'boss', 1, 'w'), (4, 'asked by boss, w has a lead', 'w', 'boss', 1, NULL)",
	)
	said(t, run("w", "task.done", "a", `,"task":1,"evidence":"x"`))
	said(t, run("human", "task.done", "b", `,"task":2,"evidence":"x"`))
	said(t, run("w", "task.done", "c", `,"task":3,"evidence":"x"`))
	if got := said(t, run("w", "task.done", "d", `,"task":4,"evidence":"built"`)); got != "task T4 done (built)" {
		t.Fatal(got)
	}
	for tid := int64(1); tid <= 4; tid++ {
		if st := taskState(t, sdb, tid); st != "done" {
			t.Fatalf("T%d is %s", tid, st)
		}
	}
	if len(f.done) != 4 {
		t.Fatalf("%v", f.done)
	}
}
