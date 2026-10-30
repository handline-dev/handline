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
