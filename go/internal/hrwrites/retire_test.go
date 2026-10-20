package hrwrites

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/gitrun"
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

// A retired member's worktree that is merged, clean and nobody else's goes with it; any other stays and the reply says why.
func TestARetiredMembersDoneWorktreeIsRemovedAndTheRestStay(t *testing.T) {
	r := newRig(t)
	root, _ := filepath.EvalSymlinks(t.TempDir())
	main := filepath.Join(root, "main")
	git := func(dir string, args ...string) {
		t.Helper()
		if out := gitrun.Run(dir, append([]string{"-c", "user.name=t", "-c", "user.email=t@t"}, args...)...); out.RC != 0 {
			t.Fatalf("git %v: %s", args, out.Stderr)
		}
	}
	os.MkdirAll(main, 0o755)
	git(main, "init", "-q", "-b", "main")
	git(main, "commit", "-q", "--allow-empty", "-m", "start")
	wt := map[string]string{}
	for _, name := range []string{"done", "ahead", "shared"} {
		wt[name] = filepath.Join(root, name)
		git(main, "worktree", "add", "-q", "-b", name, wt[name])
	}
	git(wt["ahead"], "commit", "-q", "--allow-empty", "-m", "unmerged work")
	r.db.Exec("INSERT INTO agent_state(agent, state, updated, worktree, extra_worktrees) VALUES('bob', 'idle', 1, ?, ?)",
		"repo::"+wt["done"], `["`+wt["ahead"]+`", "`+wt["shared"]+`"]`)
	r.db.Exec("INSERT INTO agent_state(agent, state, updated, worktree) VALUES('alice', 'idle', 1, ?)", wt["shared"])
	m := r.call("agent.retire", `{"agent":"bob","request_id":"w1"}`)
	res, _ := m["result"].(map[string]any)
	if res == nil {
		t.Fatalf("%v", m)
	}
	said := res["said"].(string)
	for _, want := range []string{wt["done"] + " is removed", wt["ahead"] + " stays: ahead is not merged into main", wt["shared"] + " stays: an agent works in it"} {
		if !strings.Contains(said, want) {
			t.Errorf("%q lacks %q", said, want)
		}
	}
	if _, err := os.Stat(wt["done"]); !os.IsNotExist(err) {
		t.Fatal("the done worktree is still there")
	}
	for _, name := range []string{"ahead", "shared"} {
		if _, err := os.Stat(wt[name]); err != nil {
			t.Fatalf("%s was removed", name)
		}
	}
}
