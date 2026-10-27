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

func TestAgentRetireRefusesADependedOnAgentAndRetiresOneThatIsNot(t *testing.T) {
	r := newRig(t)
	refusal := func(m map[string]any) string {
		t.Helper()
		e, _ := m["error"].(map[string]any)
		if e == nil {
			t.Fatalf("not refused: %v", m)
		}
		return e["message"].(string)
	}
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

func TestTheFounderRetiringARunningAgentIsHeldUntilItsTurnEnds(t *testing.T) {
	r := newRig(t)
	r.live["bob"] = true
	m := r.call("agent.retire", `{"agent":"bob","request_id":"h1"}`)
	if res, _ := m["result"].(map[string]any); res == nil || res["said"] != "bob is retired when it ends the turn running now." {
		t.Fatalf("accepted, not refused: %v", m)
	}
	held := r.e.listing()
	if len(held) != 1 || held[0].str("state") != "applying" || held[0].str("waiting_on") != "bob" || held[0].str("requested_by") != "human" {
		t.Fatalf("held as the founder's own approved retire: %v", held)
	}
	var started []string
	ports := HeldPorts{Alive: func(a string) bool { return r.live[a] }, Idle: func(string) bool { return false },
		Close: func(string) (bool, string) { t.Fatal("a turn is never cut"); return false, "" },
		Start: func(a string) (bool, string) { started = append(started, a); return true, "" }}
	r.e.SweepHeld(ports)
	if !strings.Contains(r.file("team.yaml"), "  bob: {}") {
		t.Fatal("bob stays while its turn runs")
	}
	r.live["bob"] = false // the turn ended and the session went
	r.e.SweepHeld(ports)
	if strings.Contains(r.file("team.yaml"), "bob") || len(started) != 0 || r.state(held[0].id()) != "applied" {
		t.Fatalf("retired, and not started again: %v %s\n%s", started, r.state(held[0].id()), r.file("team.yaml"))
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
	var asked []string
	r.e.Ask = func(title, body string, tags ...string) (int64, error) {
		asked = append(asked, title+"|"+strings.Join(tags, ",")+"|"+body)
		return int64(40 + len(asked)), nil
	}
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
	// Only the one that holds work is put to the founder; the one an agent shares is not theirs to judge.
	if len(asked) != 1 || !strings.HasPrefix(asked[0], "Remove bob's worktree?|worktree-removal|") ||
		!strings.Contains(asked[0], "Path: "+wt["ahead"]) || !strings.Contains(asked[0], "Member: bob") || !strings.Contains(asked[0], "  unmerged work") ||
		!strings.Contains(said, "asked the founder in T41") {
		t.Fatalf("asked %q\n%s", asked, said)
	}
	for _, name := range []string{"ahead", "shared"} {
		if _, err := os.Stat(wt[name]); err != nil {
			t.Fatalf("%s was removed", name)
		}
	}
}
