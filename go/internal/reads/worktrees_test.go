package reads

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/operations"
)

func TestAWorktreeLabelStandsInForTheDaemonsWord(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "alice", "")
	wt, _ := filepath.EvalSymlinks(t.TempDir())
	if _, err := e.DB.Exec("INSERT INTO agent_state(agent, state, updated, worktree) VALUES('alice', 'idle', 0, ?)", wt); err != nil {
		t.Fatal(err)
	}
	status := func() string {
		got, err := e.worktreesList(nil, map[string]any{})
		if err != nil {
			t.Fatal(err)
		}
		return got.(map[string]any)["worktrees"].([]any)[0].(map[string]any)["status"].(string)
	}
	if s := status(); s != "clean" {
		t.Fatalf("no label: %q", s)
	}
	key, ok, err := e.LabelKey(wt)
	if err != nil || !ok {
		t.Fatalf("%v %v", ok, err)
	}
	if _, err := e.DB.Exec("INSERT INTO meta(name, value, updated) VALUES(?, 'in review', 0)", key); err != nil {
		t.Fatal(err)
	}
	if s := status(); s != "in review" {
		t.Fatalf("labelled: %q", s)
	}
}

func TestARunningOperationNobodyRunsIsReportedFailed(t *testing.T) {
	e := testEnv(t)
	if _, err := e.DB.Exec("INSERT INTO operations(id, kind, state, steps_done, agent, created_at, updated_at) VALUES('gone', 'agent.create', 'running', 0, 'x', 0, 0), ('here', 'agent.create', 'running', 0, 'y', 0, 0)"); err != nil {
		t.Fatal(err)
	}
	operations.Begin("here")
	defer operations.End("here")
	for id, want := range map[string]string{"gone": "failed", "here": "running"} {
		got, err := e.operationGet(nil, map[string]any{"operation": id})
		if err != nil {
			t.Fatal(err)
		}
		if s := got.(map[string]any)["state"]; s != want {
			t.Fatalf("%s: %v, want %s", id, s, want)
		}
	}
}

func TestTheDiffOfAWholeWorktreeIncludesUntrackedFilesButNotIgnoredOnes(t *testing.T) {
	e := testEnv(t)
	prAgent(t, e, "w", "")
	row, _ := queryOne(e.DB, "SELECT worktree FROM agent_state WHERE agent = 'w'")
	dir := row.str("worktree")
	git := func(args ...string) {
		t.Helper()
		if out, err := exec.Command("git", append([]string{"-C", dir, "-c", "user.name=t", "-c", "user.email=t@t"}, args...)...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v %s", args, err, out)
		}
	}
	write := func(name, text string) {
		t.Helper()
		if err := os.WriteFile(filepath.Join(dir, name), []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	diff := func(file any) string {
		t.Helper()
		got, err := e.worktreeDiff(nil, map[string]any{"path": dir, "file": file})
		if err != nil {
			t.Fatal(err)
		}
		return got.(map[string]any)["diff"].(string)
	}
	write(".gitignore", "*.log\n")
	write("a.txt", "one\n")
	git("add", ".")
	git("commit", "-q", "-m", "first")
	write("b.txt", "brand new\n")
	write("noise.log", "ignored\n")
	got := diff(nil)
	if !strings.Contains(got, "+++ b/b.txt") || !strings.Contains(got, "+brand new") || strings.Contains(got, "noise.log") {
		t.Fatalf("the only change is one untracked file:\n%s", got)
	}
	write("a.txt", "two\n")
	if got = diff(nil); !strings.Contains(got, "+two") || !strings.Contains(got, "+++ b/b.txt") {
		t.Fatalf("a tracked change and an untracked file:\n%s", got)
	}
	if one := diff("b.txt"); !strings.Contains(got, one) {
		t.Fatalf("the per-file diff of the untracked file is part of the whole:\n%s", one)
	}
	write("dump.txt", strings.Repeat("x\n", maxDiffChars))
	got = diff(nil)
	if !strings.Contains(got, "(new file, 400000 bytes: not shown)") || strings.Contains(got, "+x\n+x") || !strings.Contains(got, "+++ b/b.txt") {
		t.Fatalf("an untracked file over the cap is listed, not diffed:\n%.300s", got)
	}
}

// A worktree of an agent's repo that no agent records is listed as an orphan, with what it holds and its measured size; owner lists none.
func TestAWorktreeNoAgentRecordsIsListedAsAnOrphan(t *testing.T) {
	e := testEnv(t)
	root, _ := filepath.EvalSymlinks(t.TempDir())
	repo, mine, left := filepath.Join(root, "repo"), filepath.Join(root, "mine"), filepath.Join(root, "left")
	git := func(dir string, args ...string) {
		t.Helper()
		if out, err := exec.Command("git", append([]string{"-C", dir, "-c", "user.name=t", "-c", "user.email=t@t"}, args...)...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v %s", args, err, out)
		}
	}
	os.MkdirAll(repo, 0o755)
	git(repo, "init", "-q", "-b", "main")
	git(repo, "commit", "-q", "--allow-empty", "-m", "start")
	git(repo, "worktree", "add", "-q", "-b", "mine", mine)
	git(repo, "worktree", "add", "-q", "-b", "left", left)
	git(left, "commit", "-q", "--allow-empty", "-m", "unmerged")
	os.WriteFile(filepath.Join(left, "new.txt"), []byte("x"), 0o644)
	addAgent(t, e, "alice", "")
	os.WriteFile(filepath.Join(e.Home, "agents", "alice", "agent.yaml"), []byte("repo: "+repo+"\n"), 0o644)
	if _, err := e.DB.Exec("INSERT INTO agent_state(agent, state, updated, worktree) VALUES('alice', 'idle', 0, ?)", mine); err != nil {
		t.Fatal(err)
	}
	list := func(params map[string]any) map[string]map[string]any {
		t.Helper()
		got, err := e.worktreesList(nil, params)
		if err != nil {
			t.Fatal(err)
		}
		out := map[string]map[string]any{}
		for _, w := range got.(map[string]any)["worktrees"].([]any) {
			out[filepath.Base(w.(map[string]any)["path"].(string))] = w.(map[string]any)
		}
		return out
	}
	all := list(map[string]any{})
	if len(all) != 2 || all["mine"]["orphan"] != nil {
		t.Fatalf("alice's worktree and one orphan, not the repo's checkout: %v", all)
	}
	o := all["left"]
	if o["orphan"] != true || o["owner"] != nil || o["merged"] != false || o["files_changed"] != 1 || o["commits_ahead"] != 1 || o["size_bytes"] != nil {
		t.Fatalf("the orphan: %v", o)
	}
	e.DB.Exec("INSERT INTO meta(name, value, updated) VALUES(?, '4096', 0)", "worktree_size:"+left)
	if size := list(map[string]any{})["left"]["size_bytes"]; size != int64(4096) {
		t.Fatalf("measured size: %v", size)
	}
	for i := 0; i < maxChanged; i++ {
		os.WriteFile(filepath.Join(left, fmt.Sprintf("more%d.txt", i)), []byte("x"), 0o644)
	}
	if o := list(map[string]any{})["left"]; o["files_changed"] != maxChanged+1 || len(o["changed"].([]any)) != maxChanged {
		t.Fatalf("files past the listing's cap still count: %v changed, %d listed", o["files_changed"], len(o["changed"].([]any)))
	}
	if mine := list(map[string]any{"owner": "alice"}); len(mine) != 1 {
		t.Fatalf("owner lists no orphan: %v", mine)
	}
	if other := list(map[string]any{"repo": "elsewhere"}); len(other) != 0 {
		t.Fatalf("another repo: %v", other)
	}
}
