package taskwrites

import (
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
)

func TestTheFoundersDoneAndDiscardNeedNoReasonAnAgentsDo(t *testing.T) {
	db, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	home := t.TempDir()
	for _, a := range []string{"w", "boss"} {
		os.MkdirAll(filepath.Join(home, "agents", a), 0o755)
		os.WriteFile(filepath.Join(home, "agents", a, "agent.yaml"), nil, 0o644)
	}
	for i := 1; i <= 7; i++ {
		if _, err := db.Exec("INSERT INTO tasks(id, title, owner, opened_by, created_at, expects) VALUES(?, 't', 'w', 'boss', 1, 'report')", i); err != nil {
			t.Fatal(err)
		}
	}
	mk := func(agent string) *rpc.Server {
		s := rpc.NewServer(auth{agent}, "x", store.LatestVersion(), "local", func(string, ...any) {})
		s.DB = db
		Register(s, home, &fakeTeller{}, nil)
		return s
	}
	n := 0
	send := func(agent, method string, task int, params string) map[string]any {
		n++
		return call(t, mk(agent), fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":%q,"params":{"task":%d,"request_id":"r%d"%s}}`, method, task, n, params))[0]
	}
	said := func(r map[string]any) string {
		if r["error"] != nil {
			t.Fatalf("refused: %v", r)
		}
		return r["result"].(map[string]any)["said"].(string)
	}
	// the founder
	if got := said(send("", "task.done", 1, ``)); got != "task T1 done (Done)" {
		t.Errorf("done with nothing: %q", got)
	}
	if got := said(send("", "task.done", 2, `,"evidence":"  "`)); got != "task T2 done (Done)" {
		t.Errorf("done with blank words: %q", got)
	}
	if got := said(send("", "task.discard", 3, ``)); got != "task T3 discarded by human: Discarded" {
		t.Errorf("discard with nothing: %q", got)
	}
	if got := said(send("", "task.discard", 4, `,"reason":" "`)); got != "task T4 discarded by human: Discarded" {
		t.Errorf("discard with a blank reason: %q", got)
	}
	if got := said(send("", "task.discard", 5, `,"reason":"dup"`)); got != "task T5 discarded by human: dup" {
		t.Errorf("a given reason is kept: %q", got)
	}
	if r := send("", "task.done", 6, `,"evidence":7`); r["error"] == nil {
		t.Errorf("evidence that is not text is refused for the founder too: %v", r)
	}
	// an agent still gives one
	if r := send("w", "task.done", 6, ``); r["error"] == nil {
		t.Errorf("an agent's done with no evidence: %v", r)
	}
	if r := send("boss", "task.discard", 6, ``); r["error"] == nil {
		t.Errorf("an agent's discard with no reason: %v", r)
	}
	if r := send("boss", "task.discard", 6, `,"reason":" "`); r["error"] == nil {
		t.Errorf("an agent's discard with a blank reason: %v", r)
	}
}
