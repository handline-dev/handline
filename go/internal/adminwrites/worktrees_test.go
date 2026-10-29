package adminwrites

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/reads"
)

func TestSetStatusLabelsAWorktreeAnAgentWorksIn(t *testing.T) {
	r := newRig(t)
	wt, _ := filepath.EvalSymlinks(t.TempDir())
	if _, err := r.e.DB.Exec("INSERT INTO agent_state(agent, state, updated, worktree) VALUES('alice', 'idle', 0, ?)", wt); err != nil {
		t.Fatal(err)
	}
	set := func(status, id string) map[string]any {
		return r.one("worktree.set_status", fmt.Sprintf(`{"path":%q,"status":%q,"request_id":%q}`, wt, status, id))
	}
	if res := r.result(set("in review", "w1")); res["accepted"] != true {
		t.Fatalf("%v", res)
	}
	var got string
	r.e.DB.QueryRow("SELECT value FROM meta WHERE name = ?", reads.StatusLabelPrefix+wt).Scan(&got)
	if got != "in review" {
		t.Fatalf("label %q", got)
	}
	r.result(set("done", "w2"))
	r.e.DB.QueryRow("SELECT value FROM meta WHERE name = ?", reads.StatusLabelPrefix+wt).Scan(&got)
	if got != "done" {
		t.Fatalf("set again: %q", got)
	}
	if r.result(set("done", "w2"))["replayed"] != true {
		t.Fatal("a retry replays")
	}
	for _, bad := range []string{"", strings.Repeat("x", 41), "two\nlines"} {
		if c := r.code(set(bad, "w-"+bad[:min(len(bad), 3)])); c != "bad_request" {
			t.Fatalf("status %q: %s", bad, c)
		}
	}
	if c := r.code(r.one("worktree.set_status", `{"path":"/","status":"done","request_id":"w9"}`)); c != "not_found" {
		t.Fatalf("a path no agent works in: %s", c)
	}
	if _, err := os.Stat(wt); err != nil {
		t.Fatal(err)
	}
}

// worktree.remove takes an orphan: a merged, clean one at once with its branch; one holding work is refused (gate dirty) unless force,
// which leaves its branch; an agent's worktree is refused (gate orphan).
func TestRemoveTakesAnOrphanCleanAtOnceAndOneHoldingWorkOnlyByForce(t *testing.T) {
	r := newRig(t)
	root, _ := filepath.EvalSymlinks(t.TempDir())
	repo := filepath.Join(root, "repo")
	git := func(dir string, args ...string) bool {
		t.Helper()
		return exec.Command("git", append([]string{"-C", dir, "-c", "user.name=t", "-c", "user.email=t@t"}, args...)...).Run() == nil
	}
	os.MkdirAll(repo, 0o755)
	git(repo, "init", "-q", "-b", "main")
	git(repo, "commit", "-q", "--allow-empty", "-m", "start")
	wt := map[string]string{}
	for _, name := range []string{"mine", "done", "ahead"} {
		wt[name] = filepath.Join(root, name)
		git(repo, "worktree", "add", "-q", "-b", name, wt[name])
	}
	git(wt["ahead"], "commit", "-q", "--allow-empty", "-m", "unmerged")
	os.MkdirAll(filepath.Join(r.home, "agents", "alice"), 0o755)
	os.WriteFile(filepath.Join(r.home, "agents", "alice", "agent.yaml"), []byte("repo: "+repo+"\n"), 0o644)
	r.e.DB.Exec("INSERT INTO agent_state(agent, state, updated, worktree) VALUES('alice', 'idle', 0, ?)", wt["mine"])
	remove := func(path, id, force string) map[string]any {
		return r.one("worktree.remove", fmt.Sprintf(`{"path":%q,"request_id":%q%s}`, path, id, force))
	}
	data := func(m map[string]any) map[string]any {
		t.Helper()
		e, _ := m["error"].(map[string]any)
		d, _ := e["data"].(map[string]any)
		return d
	}
	gone := func(name string) bool { _, err := os.Stat(wt[name]); return os.IsNotExist(err) }
	branch := func(name string) bool { return git(repo, "rev-parse", "--verify", "-q", "refs/heads/"+name) }

	if d := data(remove(wt["mine"], "r1", "")); d["gate"] != "orphan" || gone("mine") {
		t.Fatalf("an agent's worktree: %v", d)
	}
	if res := r.result(remove(wt["done"], "r2", "")); res["branch_deleted"] != true || !gone("done") || branch("done") {
		t.Fatalf("merged and clean: %v", res)
	}
	if d := data(remove(wt["ahead"], "r3", "")); d["gate"] != "dirty" || d["commits_ahead"] != float64(1) || d["files_changed"] != float64(0) || gone("ahead") {
		t.Fatalf("holding work: %v", d)
	}
	if res := r.result(remove(wt["ahead"], "r4", `,"force":true`)); res["branch_deleted"] != false || !gone("ahead") || !branch("ahead") {
		t.Fatalf("forced: %v, the unmerged branch stays", res)
	}
}

// An agent records a worktree it works in beside its start dir: worktree.remove no longer takes it as an orphan, a second record is said
// so, and a path that is no worktree of an agent's repo, the repo's own checkout or a person's call is refused.
func TestAnAgentDeclaresAWorktreeItWorksInSoItIsNoOrphan(t *testing.T) {
	r := newRig(t)
	root, _ := filepath.EvalSymlinks(t.TempDir())
	repo, mine, used := filepath.Join(root, "repo"), filepath.Join(root, "mine"), filepath.Join(root, "used")
	os.MkdirAll(repo, 0o755)
	for _, args := range [][]string{{"init", "-q", "-b", "main"}, {"commit", "-q", "--allow-empty", "-m", "start"},
		{"worktree", "add", "-q", "-b", "mine", mine}, {"worktree", "add", "-q", "-b", "used", used}} {
		if out, err := exec.Command("git", append([]string{"-C", repo, "-c", "user.name=t", "-c", "user.email=t@t"}, args...)...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v %s", args, err, out)
		}
	}
	os.WriteFile(filepath.Join(r.home, "agents", "alice", "agent.yaml"), []byte("repo: "+repo+"\n"), 0o644)
	r.e.DB.Exec("INSERT INTO agent_state(agent, state, updated, worktree, extra_worktrees) VALUES('alice', 'idle', 0, ?, '[\"/kept/extra\"]')", mine)
	declare := func(path, id string) map[string]any {
		return r.one("worktree.declare", fmt.Sprintf(`{"path":%q,"request_id":%q}`, path, id))
	}
	gate := func(m map[string]any) any {
		e, _ := m["error"].(map[string]any)
		d, _ := e["data"].(map[string]any)
		return d["gate"]
	}

	if g := gate(declare(used, "d0")); g != "role" {
		t.Fatalf("a person's call: %v", g)
	}
	r.as = "alice"
	if res := r.result(declare(used, "d1")); res["said"] != used+" is recorded as one of alice's worktrees" {
		t.Fatalf("%v", res)
	}
	var extras string
	r.e.DB.QueryRow("SELECT extra_worktrees FROM agent_state WHERE agent = 'alice'").Scan(&extras)
	if extras != `["/kept/extra", "`+used+`"]` {
		t.Fatalf("extra_worktrees %s", extras)
	}
	if res := r.result(declare(used, "d2")); res["said"] != used+" is already one of alice's worktrees" {
		t.Fatalf("again: %v", res)
	}
	for i, path := range []string{repo, filepath.Join(root, "elsewhere")} {
		if c := r.code(declare(path, fmt.Sprint("n", i))); c != "not_found" {
			t.Fatalf("%s: %s", path, c)
		}
	}
	r.as = ""
	if g := gate(r.one("worktree.remove", fmt.Sprintf(`{"path":%q,"request_id":"x1","force":true}`, used))); g != "orphan" {
		t.Fatalf("a declared worktree is no orphan: %v", g)
	}
}
