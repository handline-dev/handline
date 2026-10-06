package reads

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/store"
)

func testEnv(t *testing.T) *Env {
	t.Helper()
	home := t.TempDir()
	db, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })
	return &Env{DB: db, Home: home, Started: time.Now()}
}

func addAgent(t *testing.T, e *Env, name, role string) {
	t.Helper()
	dir := filepath.Join(e.Home, "agents", name)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	os.WriteFile(filepath.Join(dir, "agent.yaml"), nil, 0o644)
	if role != "" {
		os.WriteFile(filepath.Join(dir, "ROLE.md"), []byte(role), 0o644)
	}
}

func TestAnAgentWithNoStoredViewIsListedWithItsRoleAndAnUnknownState(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "worker", "# worker\n\n  Does   the work.\nSecond.\n")
	addAgent(t, e, "manager", "")
	if _, err := e.DB.Exec(`INSERT INTO agent_view(agent, view, updated) VALUES('manager', '{"name":"manager","state":"IDLE"}', 0)`); err != nil {
		t.Fatal(err)
	}
	got, err := e.agentsList(nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := json.Marshal(got)
	var out struct{ Agents []map[string]any }
	json.Unmarshal(b, &out)
	if len(out.Agents) != 2 || out.Agents[0]["state"] != "IDLE" || out.Agents[1]["name"] != "worker" ||
		out.Agents[1]["state"] != "unknown" || out.Agents[1]["role"] != "Does   the work." {
		t.Fatalf("%s", b)
	}
}

func TestAnAgentThatIsNotRegisteredIsNotFound(t *testing.T) {
	e := testEnv(t)
	if _, err := e.named(map[string]any{"agent": "ghost"}); err == nil || err.Error() == "" {
		t.Fatal("a missing agent was found")
	}
	if _, err := e.named(map[string]any{}); err == nil {
		t.Fatal("no agent param was accepted")
	}
}

func TestTheTaskLadderMatchesTheBoard(t *testing.T) {
	b := &board{begun: map[int64]bool{3: true}, sent: map[int64]bool{2: true}, children: map[int64][]int64{5: {2}},
		current: map[string]int64{"w": 9}, held: map[int64]row{9: {"done_evidence": "x"}}}
	mk := func(id int64, owner string, extra row) row {
		r := row{"id": id, "owner": owner}
		for k, v := range extra {
			r[k] = v
		}
		return r
	}
	for _, c := range []struct {
		t    row
		want string
	}{
		{mk(1, "w", nil), "draft"},
		{mk(1, "w", row{"queued_by": "m"}), "queued"},
		{mk(2, "w", nil), "doing"},                       // a request is out and its owner's held task is over
		{mk(3, "human", nil), "doing"},                   // begun on a founder's message
		{mk(5, "w", nil), "doing"},                       // an open child has one out
		{mk(2, "w", row{"done_evidence": "ok"}), "done"}, // written states win
		{mk(2, "w", row{"discarded_by": "m", "done_evidence": "ok"}), "discarded"},
	} {
		if got := b.state(c.t); got != c.want {
			t.Errorf("%v: %s, want %s", c.t, got, c.want)
		}
	}
	b.held[9] = row{}
	if got := b.state(mk(2, "w", nil)); got != "queued" {
		t.Errorf("a task its owner does not hold reads %s, not queued", got)
	}
}

func TestDurationsAndAges(t *testing.T) {
	for in, want := range map[int64]string{7200: "2h", 5400: "90m", 86400: "1d", 61: "61s"} {
		if got := duration(in); got != want {
			t.Errorf("duration(%d) = %s, want %s", in, got, want)
		}
	}
	for in, want := range map[int64]string{5: "5s", 130: "2m", 7200: "2h", 86400 + 3600: "1d1h"} {
		if got := ago(in); got != want {
			t.Errorf("ago(%d) = %s, want %s", in, got, want)
		}
	}
}
