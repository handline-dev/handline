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

// bobsWorktrees is a repo with bob's three worktrees: done (merged, clean), ahead (a commit not on main) and shared (alice works in it too).
func bobsWorktrees(t *testing.T, r *rig) (main string, wt map[string]string, git func(dir string, args ...string) bool) {
	t.Helper()
	root, _ := filepath.EvalSymlinks(t.TempDir())
	main = filepath.Join(root, "main")
	git = func(dir string, args ...string) bool {
		return gitrun.Run(dir, append([]string{"-c", "user.name=t", "-c", "user.email=t@t"}, args...)...).RC == 0
	}
	os.MkdirAll(main, 0o755)
	git(main, "init", "-q", "-b", "main")
	git(main, "commit", "-q", "--allow-empty", "-m", "start")
	wt = map[string]string{}
	for _, name := range []string{"done", "ahead", "shared"} {
		wt[name] = filepath.Join(root, name)
		git(main, "worktree", "add", "-q", "-b", name, wt[name])
	}
	git(wt["ahead"], "commit", "-q", "--allow-empty", "-m", "unmerged work")
	r.db.Exec("INSERT INTO agent_state(agent, state, updated, worktree, extra_worktrees) VALUES('bob', 'idle', 1, ?, ?)",
		"repo::"+wt["done"], `["`+wt["ahead"]+`", "`+wt["shared"]+`"]`)
	r.db.Exec("INSERT INTO agent_state(agent, state, updated, worktree) VALUES('alice', 'idle', 1, ?)", wt["shared"])
	return main, wt, git
}

// The founder's retire asks about a worktree that holds work at once: with no answer it is refused (gate dirty) naming each such
// worktree and what it holds, and nothing is retired.
func TestTheFoundersRetireIsRefusedWhileAWorktreeHoldsWorkWithNoAnswer(t *testing.T) {
	r := newRig(t)
	_, wt, _ := bobsWorktrees(t, r)
	m := r.call("agent.retire", `{"agent":"bob","request_id":"w0"}`)
	e, _ := m["error"].(map[string]any)
	d, _ := e["data"].(map[string]any)
	list, _ := d["worktrees"].([]any)
	if d["gate"] != "dirty" || len(list) != 1 {
		t.Fatalf("%v", m)
	}
	if h := list[0].(map[string]any); h["path"] != wt["ahead"] || h["commits_ahead"] != float64(1) || h["files_changed"] != float64(0) {
		t.Fatalf("%v", h)
	}
	if !strings.Contains(r.file("team.yaml"), "  bob: {}") {
		t.Fatal("bob was retired")
	}
	if m := r.call("agent.retire", `{"agent":"bob","worktree":"maybe","request_id":"w0b"}`); m["error"] == nil {
		t.Fatalf("an answer that is none of keep, save, force: %v", m)
	}
}

// With an answer the retire goes on: a merged, clean worktree goes with its branch, one that holds work is kept, saved to a rescue
// branch or forced away as answered, and one another agent shares stays. Nothing is put to the founder as a task.
func TestTheFoundersAnswerIsCarriedOutForAWorktreeThatHoldsWork(t *testing.T) {
	for _, c := range []struct{ choice, said string }{
		{"keep", " stays, as asked"}, {"save", " is removed; what it held is on rescue/bob-"}, {"force", " is removed with what it held"},
	} {
		t.Run(c.choice, func(t *testing.T) {
			r := newRig(t)
			main, wt, git := bobsWorktrees(t, r)
			r.e.Ask = func(title, body string, tags ...string) (int64, error) {
				t.Fatal("asked the founder: " + title)
				return 0, nil
			}
			m := r.call("agent.retire", `{"agent":"bob","worktree":"`+c.choice+`","request_id":"w1"}`)
			res, _ := m["result"].(map[string]any)
			if res == nil {
				t.Fatalf("%v", m)
			}
			said, _ := res["said"].(string)
			for _, want := range []string{wt["done"] + " is removed with its branch done", wt["ahead"] + c.said, wt["shared"] + " stays: an agent works in it"} {
				if !strings.Contains(said, want) {
					t.Errorf("%q lacks %q", said, want)
				}
			}
			_, err := os.Stat(wt["ahead"])
			if kept := err == nil; kept != (c.choice == "keep") {
				t.Fatalf("ahead kept %v", kept)
			}
			if git(main, "rev-parse", "--verify", "-q", "refs/heads/done") || !git(main, "rev-parse", "--verify", "-q", "refs/heads/ahead") {
				t.Fatal("the merged branch goes, the unmerged one stays")
			}
		})
	}
}

// A running agent retired with an answer is held with it: when its turn ends, SweepHeld carries the answer out, and nobody is asked again.
func TestARunningAgentsRetireKeepsTheFoundersAnswerForItsTurnsEnd(t *testing.T) {
	r := newRig(t)
	_, wt, _ := bobsWorktrees(t, r)
	r.live["bob"] = true
	r.e.Ask = func(title, body string, tags ...string) (int64, error) {
		t.Fatal("asked the founder again: " + title)
		return 0, nil
	}
	if m := r.call("agent.retire", `{"agent":"bob","worktree":"force","request_id":"h2"}`); m["result"] == nil {
		t.Fatalf("%v", m)
	}
	ports := HeldPorts{Alive: func(a string) bool { return r.live[a] }, Idle: func(string) bool { return false },
		Close: func(string) (bool, string) { return false, "" }, Start: func(string) (bool, string) { return true, "" }}
	r.live["bob"] = false // the turn ended and the session went
	r.e.SweepHeld(ports)
	if _, err := os.Stat(wt["ahead"]); err == nil || strings.Contains(r.file("team.yaml"), "  bob: {}") {
		t.Fatalf("bob retired and its worktree forced away (stat err %v)\n%s", err, r.file("team.yaml"))
	}
}

// With nobody there to ask (an HR retire, split, rename or move), a worktree that holds work is put to the founder as a task.
func TestWithNoAnswerAWorktreeThatHoldsWorkIsAskedAsATask(t *testing.T) {
	r := newRig(t)
	_, wt, _ := bobsWorktrees(t, r)
	var asked []string
	r.e.Ask = func(title, body string, tags ...string) (int64, error) {
		asked = append(asked, title+"|"+strings.Join(tags, ",")+"|"+body)
		return int64(40 + len(asked)), nil
	}
	said := r.e.pruneMemberWorktrees("bob", []string{wt["done"], wt["ahead"], wt["shared"]}, "")
	if len(asked) != 1 || !strings.HasPrefix(asked[0], "Remove bob's worktree?|worktree-removal|") ||
		!strings.Contains(asked[0], "Path: "+wt["ahead"]) || !strings.Contains(said, wt["ahead"]+" stays: ahead is not merged into main (asked the founder in T41)") { // local-ref-ok: the fake Ask numbers tasks from T41
		t.Fatalf("asked %q\n%s", asked, said)
	}
	if _, err := os.Stat(wt["ahead"]); err != nil {
		t.Fatal("ahead was removed before the answer")
	}
}
