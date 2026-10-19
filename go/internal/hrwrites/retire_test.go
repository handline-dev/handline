package hrwrites

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/sealed"
	"github.com/handline/handline/go/internal/team"
)

func TestAgentRetireRefusesARunningOrDependedOnAgentAndRetiresOneThatIsNot(t *testing.T) {
	r := newRig(t)
	refusal := func(m map[string]any) string {
		t.Helper()
		e, _ := m["error"].(map[string]any)
		if e == nil {
			t.Fatalf("not refused: %v", m)
		}
		return e["message"].(string)
	}
	r.live["bob"] = true
	if msg := refusal(r.call("agent.retire", `{"agent":"bob","request_id":"r1"}`)); !strings.Contains(msg, "bob has a session running") {
		t.Fatal(msg)
	}
	r.live["bob"] = false
	// bob.kid hangs under bob's name; a supervisor edge from bob no longer makes alice its report
	withEdge := strings.NewReplacer("  bob: {}\n", "  bob: {}\n  bob.kid: {}\n",
		"edges:\n", "edges:\n  - { from: \"bob\", to: \"alice\", relation: supervisor }\n").Replace(teamYAML)
	if err := os.WriteFile(filepath.Join(r.home, "team.yaml"), []byte(withEdge), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := sealed.New(r.home).Seal(sealed.Team, withEdge); err != nil {
		t.Fatal(err)
	}
	if _, err := team.NewRoster(r.home).Register("bob.kid", team.RegisterOpts{Role: "r"}); err != nil {
		t.Fatal(err)
	}
	if msg := refusal(r.call("agent.retire", `{"agent":"bob","request_id":"r2"}`)); !strings.Contains(msg, "bob has agents under it (bob.kid)") {
		t.Fatal(msg)
	}
	if !strings.Contains(r.file("team.yaml"), "  bob: {}") {
		t.Fatal("a refusal retired nothing")
	}
	// bob.kid is retired: nothing depends on it
	r.db.Exec("INSERT INTO agent_state(agent, state, updated) VALUES('bob.kid', 'idle', 1)")
	m := r.call("agent.retire", `{"agent":"bob.kid","request_id":"r3"}`)
	res, _ := m["result"].(map[string]any)
	archive := filepath.Join(r.home, "agents", "archive", time.Now().Format("2006-01-02")+"-bob.kid")
	if res == nil || !strings.HasPrefix(res["said"].(string), "bob.kid is retired; its folder is at "+archive) {
		t.Fatalf("%v", m)
	}
	if _, err := os.Stat(filepath.Join(archive, "agent.yaml")); err != nil {
		t.Fatal("the folder is archived:", err)
	}
	var rows int
	r.db.QueryRow("SELECT COUNT(*) FROM agent_state WHERE agent = 'bob.kid'").Scan(&rows)
	if rows != 0 || strings.Contains(r.file("team.yaml"), "bob.kid") || r.file("team.yaml.bak") == "" {
		t.Fatalf("rows %d\n%s", rows, r.file("team.yaml"))
	}
	again, _ := r.call("agent.retire", `{"agent":"bob.kid","request_id":"r3"}`)["result"].(map[string]any)
	if again == nil || again["replayed"] != true {
		t.Fatalf("a retry is the stored reply: %v", again)
	}
	if msg := refusal(r.call("agent.retire", `{"agent":"bob.kid","request_id":"r4"}`)); !strings.Contains(msg, "no such agent") {
		t.Fatal(msg)
	}
}
