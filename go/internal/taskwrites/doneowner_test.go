package taskwrites

import (
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
)

// A task the founder holds is closed from under them by its parent's owner or a lead of its team, never by a stranger, and the founder hears it.
func TestFounderHeldTaskIsClosedByItsParentOwnerOrALead(t *testing.T) {
	db, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	home := t.TempDir()
	for _, a := range []string{"lead", "w", "pa", "x"} {
		os.MkdirAll(filepath.Join(home, "agents", a), 0o755)
		os.WriteFile(filepath.Join(home, "agents", a, "agent.yaml"), nil, 0o644)
	}
	os.WriteFile(filepath.Join(home, "team.yaml"), []byte("teams:\n  lead: [lead, w]\nagents:\n  lead: {}\n  w: {}\n  pa: {}\n  x: {}\n"), 0o644)
	for _, q := range []string{
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, expects) VALUES(1, 'parent', 'pa', 'pa', 1, 'report'), (2, 'kid', 'human', 'w', 1, 'report'), " +
			"(3, 'lead case', 'human', 'w', 1, 'report'), (4, 'stranger', 'human', 'w', 1, 'report')",
		"UPDATE tasks SET parent_task = 1 WHERE id = 2",
	} {
		if _, err := db.Exec(q); err != nil {
			t.Fatalf("%s: %v", q, err)
		}
	}
	f := &fakeTeller{}
	done := func(agent string, task int) map[string]any {
		s := rpc.NewServer(auth{agent}, "x", store.LatestVersion(), "local", func(string, ...any) {})
		s.DB = db
		Register(s, home, f, nil)
		return call(t, s, fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":"task.done","params":{"task":%d,"request_id":"r%s%d","evidence":"taken back"}}`, task, agent, task))[0]
	}
	if r := done("x", 4); r["error"] == nil {
		t.Fatalf("a stranger closed a founder-held task: %v", r)
	}
	for _, c := range []struct {
		agent string
		task  int
	}{{"pa", 2}, {"lead", 3}} {
		if r := done(c.agent, c.task); r["error"] != nil {
			t.Fatalf("%s could not close T%d: %v", c.agent, c.task, r)
		}
		var n int
		db.QueryRow(fmt.Sprintf("SELECT COUNT(*) FROM requests WHERE target = 'human' AND body LIKE 'T%d done by %s: taken back'", c.task, c.agent)).Scan(&n)
		if n != 1 {
			t.Fatalf("the founder was not told in one line that %s closed T%d", c.agent, c.task)
		}
	}
}
