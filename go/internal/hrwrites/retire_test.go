package hrwrites

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/gitrun"
	"github.com/handline/handline/go/internal/sealed"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/yamlcompat"
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

// With an answer the retire goes on: its worktrees go first, a merged, clean one with its branch, one that holds work saved to a rescue
// branch or forced away as answered, and one another agent shares is left alone. keep leaves the worktree, so the agent stays too.
// Nothing is put to the founder as a task.
func TestTheFoundersAnswerIsCarriedOutForAWorktreeThatHoldsWork(t *testing.T) {
	for _, c := range []struct{ choice, said string }{
		{"save", " is removed; what it held is on rescue/bob-"}, {"force", " is removed with what it held"},
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
			for _, want := range []string{wt["done"] + " is removed with its branch done", wt["ahead"] + c.said} {
				if !strings.Contains(said, want) {
					t.Errorf("%q lacks %q", said, want)
				}
			}
			for name, gone := range map[string]bool{"done": true, "ahead": true, "shared": false} {
				if _, err := os.Stat(wt[name]); (err != nil) != gone {
					t.Errorf("%s gone %v", name, err != nil)
				}
			}
			if git(main, "rev-parse", "--verify", "-q", "refs/heads/done") || !git(main, "rev-parse", "--verify", "-q", "refs/heads/ahead") {
				t.Fatal("the merged branch goes, the unmerged one stays")
			}
		})
	}
	t.Run("keep", func(t *testing.T) {
		r := newRig(t)
		_, wt, _ := bobsWorktrees(t, r)
		m := r.call("agent.retire", `{"agent":"bob","worktree":"keep","request_id":"w1"}`)
		if e, _ := m["error"].(map[string]any); e == nil || !strings.Contains(e["message"].(string), wt["ahead"]+" holds work (ahead is not merged into main) and is kept, as asked, so bob is not deleted") {
			t.Fatalf("%v", m)
		}
		assertNotRetired(t, r, wt)
	})
}

// assertNotRetired is bob still on the team with each of its worktrees where it was.
func assertNotRetired(t *testing.T, r *rig, wt map[string]string) {
	t.Helper()
	if !strings.Contains(r.file("team.yaml"), "  bob: {}") {
		t.Fatal("bob was retired")
	}
	for name, path := range wt {
		if _, err := os.Stat(path); err != nil {
			t.Fatalf("%s was removed: %v", name, err)
		}
	}
}

// A worktree that cannot be removed keeps the agent: refused before anything changes when it is locked, and put back as it was when git
// fails to remove it; an HR retire nobody answers for held work is refused the same way, and no task is opened.
func TestAnAgentWhoseWorktreeCannotBeRemovedIsNotDeleted(t *testing.T) {
	r := newRig(t)
	main, wt, git := bobsWorktrees(t, r)
	r.e.Ask = func(title, body string, tags ...string) (int64, error) {
		t.Fatal("asked the founder: " + title)
		return 0, nil
	}
	git(main, "worktree", "lock", wt["done"])
	m := r.call("agent.retire", `{"agent":"bob","worktree":"save","request_id":"l1"}`)
	e, _ := m["error"].(map[string]any)
	if d, _ := e["data"].(map[string]any); d["gate"] != "worktree" || !strings.Contains(e["message"].(string), wt["done"]+" cannot be removed (locked)") {
		t.Fatalf("a locked worktree: %v", m)
	}
	assertNotRetired(t, r, wt)
	git(main, "worktree", "unlock", wt["done"])

	// done's folder cannot be deleted: git refuses the removal once the retire is under way.
	root := filepath.Dir(wt["done"])
	os.Chmod(root, 0o555)
	t.Cleanup(func() { os.Chmod(root, 0o755) })
	m = r.call("agent.retire", `{"agent":"bob","worktree":"save","request_id":"l2"}`)
	if e, _ := m["error"].(map[string]any); e == nil || !strings.Contains(e["message"].(string), wt["done"]+" could not be removed (") ||
		!strings.Contains(e["message"].(string), "so bob is not deleted") {
		t.Fatalf("git failing: %v", m)
	}
	os.Chmod(root, 0o755)
	assertNotRetired(t, r, wt)

	prop := &Prop{pairs: []yamlcompat.Pair{{Key: "kind", Val: "retire"}, {Key: "agent", Val: "bob"}, {Key: "id", Val: "hr1"}}}
	if ok, said, err := r.e.run(prop); err != nil || ok || !strings.Contains(said, wt["ahead"]+" holds work (ahead is not merged into main): answer save or force") {
		t.Fatalf("an HR retire with held work: %v %q %v", ok, said, err)
	}
	assertNotRetired(t, r, wt)
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

// With nobody there to ask (a worktree an agent left for another), a worktree that holds work is put to the founder as a task.
func TestWithNoAnswerAWorktreeThatHoldsWorkIsAskedAsATask(t *testing.T) {
	r := newRig(t)
	_, wt, _ := bobsWorktrees(t, r)
	var asked []string
	r.e.Ask = func(title, body string, tags ...string) (int64, error) {
		asked = append(asked, title+"|"+strings.Join(tags, ",")+"|"+body)
		return int64(40 + len(asked)), nil
	}
	said := r.e.pruneMemberWorktrees("bob", []string{wt["done"], wt["ahead"], wt["shared"]})
	if len(asked) != 1 || !strings.HasPrefix(asked[0], "Remove bob's worktree?|worktree-removal|") ||
		!strings.Contains(asked[0], "Path: "+wt["ahead"]) || !strings.Contains(asked[0], "holds work that is not on main: 0 files changed, 1 commit not on main.") ||
		!strings.Contains(asked[0], "\nforce  Force delete:") || !strings.Contains(said, wt["ahead"]+" stays: ahead is not merged into main (asked the founder in T41)") { // local-ref-ok: the fake Ask numbers tasks from T41
		t.Fatalf("asked %q\n%s", asked, said)
	}
	if _, err := os.Stat(wt["ahead"]); err != nil {
		t.Fatal("ahead was removed before the answer")
	}
}

// An agent that launches in a new worktree leaves its old one (db.WorktreeLeft); the tick judges it as a retired agent's: merged and
// clean it goes with its branch, holding work it is put to the founder, and one another agent records is left alone. Each is judged once.
func TestAWorktreeAnAgentLeftForAnotherIsJudged(t *testing.T) {
	r := newRig(t)
	main, wt, git := bobsWorktrees(t, r)
	var asked []string
	r.e.Ask = func(title, body string, tags ...string) (int64, error) { asked = append(asked, title); return 50, nil }
	r.db.Exec("UPDATE agent_state SET worktree = ?, extra_worktrees = NULL WHERE agent = 'bob'", wt["done"])
	moved := filepath.Join(filepath.Dir(main), "next")
	for _, step := range []string{wt["ahead"], wt["shared"], moved} { // done -> ahead -> shared -> next
		if err := db.SetTerminal(r.db, "bob", "", step); err != nil {
			t.Fatal(err)
		}
	}
	if err := r.e.SweepLeftWorktrees(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(wt["done"]); !os.IsNotExist(err) || git(main, "rev-parse", "--verify", "-q", "refs/heads/done") {
		t.Fatal("the merged, clean worktree it left is still there, or its branch")
	}
	if _, err := os.Stat(wt["ahead"]); err != nil || len(asked) != 1 {
		t.Fatalf("the one holding work stays and is asked about once: %v", asked)
	}
	if _, err := os.Stat(wt["shared"]); err != nil {
		t.Fatal("alice's worktree was removed")
	}
	if err := r.e.SweepLeftWorktrees(); err != nil || len(asked) != 1 {
		t.Fatalf("judged again: %v %v", asked, err)
	}
}

// An agent that works in the repository's own checkout is deleted and the checkout stays: it is no worktree to remove.
func TestAnAgentInTheRepositorysOwnCheckoutIsDeletedAndTheCheckoutStays(t *testing.T) {
	r := newRig(t)
	main, _, _ := bobsWorktrees(t, r)
	r.db.Exec("UPDATE agent_state SET worktree = ?, extra_worktrees = NULL WHERE agent = 'bob'", main)
	if m := r.call("agent.retire", `{"agent":"bob","request_id":"c1"}`); m["result"] == nil {
		t.Fatalf("%v", m)
	}
	if _, err := os.Stat(filepath.Join(main, ".git")); err != nil || strings.Contains(r.file("team.yaml"), "  bob: {}") {
		t.Fatalf("bob retired and its checkout kept (stat err %v)", err)
	}
}
