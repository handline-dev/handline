package reads

import (
	"context"
	"errors"
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
	git := gitIn(t)
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

// Every worktree, an agent's too, carries its base, commits not on it, changed files, measured size and the pull request of its branch;
// a gh that cannot answer gives pr null, and the answer is kept a minute.
func TestEveryWorktreeCarriesItsFactsAndItsPullRequest(t *testing.T) {
	e := testEnv(t)
	root, _ := filepath.EvalSymlinks(t.TempDir())
	repo, mine := filepath.Join(root, "repo"), filepath.Join(root, "mine")
	git := gitIn(t)
	os.MkdirAll(repo, 0o755)
	git(repo, "init", "-q", "-b", "main")
	git(repo, "remote", "add", "origin", "git@github.com:acme/app.git")
	git(repo, "commit", "-q", "--allow-empty", "-m", "start")
	git(repo, "worktree", "add", "-q", "-b", "story/x", mine)
	git(mine, "commit", "-q", "--allow-empty", "-m", "one")
	os.WriteFile(filepath.Join(mine, "new.txt"), []byte("x"), 0o644)
	addAgent(t, e, "alice", "")
	os.WriteFile(filepath.Join(e.Home, "agents", "alice", "agent.yaml"), []byte("repo: "+repo+"\n"), 0o644)
	if _, err := e.DB.Exec("INSERT INTO agent_state(agent, state, updated, worktree) VALUES('alice', 'idle', 0, ?)", mine); err != nil {
		t.Fatal(err)
	}
	e.DB.Exec("INSERT INTO meta(name, value, updated) VALUES(?, '2048', 0)", "worktree_size:"+mine)
	asked := 0
	var answer map[string]any
	var fail error
	e.BranchPR = func(_ context.Context, r, branch string) (map[string]any, error) {
		asked++
		if r != "acme/app" || branch != "story/x" {
			t.Errorf("asked about %s %s", r, branch)
		}
		return answer, fail
	}
	mineRow := func() map[string]any {
		t.Helper()
		got, err := e.worktreesList(nil, map[string]any{"owner": "alice"})
		if err != nil {
			t.Fatal(err)
		}
		return got.(map[string]any)["worktrees"].([]any)[0].(map[string]any)
	}
	answer = map[string]any{"number": 7, "url": "https://github.com/acme/app/pull/7", "verdict": "checks_failing", "failing": 2}
	w := mineRow()
	if w["base"] != "main" || w["commits_ahead"] != 1 || w["files_changed"] != 1 || w["size_bytes"] != int64(2048) || w["orphan"] != nil {
		t.Fatalf("facts: %v", w)
	}
	if pr, _ := w["pr"].(map[string]any); pr["verdict"] != "checks_failing" || pr["failing"] != 2 || pr["number"] != 7 {
		t.Fatalf("pr: %v", w["pr"])
	}
	answer = nil
	if mineRow()["pr"] == nil || asked != 1 {
		t.Fatalf("a minute's answer is kept: asked %d", asked)
	}
	e.branchPRs = newBranchPRs()
	fail = errors.New("gh: not logged in")
	if w := mineRow(); w["pr"] != nil {
		t.Fatalf("gh failing is pr null, never a guess: %v", w["pr"])
	}
}

type check = struct{ Conclusion, State string }

func TestAPullRequestsVerdictIsWhatHoldsIt(t *testing.T) {
	failing := func(conclusions ...string) (out []check) {
		for _, c := range conclusions {
			out = append(out, check{Conclusion: c})
		}
		return out
	}
	for _, c := range []struct {
		name string
		pr   ghPull
		want string
	}{
		{"merged", ghPull{State: "MERGED", IsDraft: true}, "merged"},
		{"draft", ghPull{State: "OPEN", IsDraft: true}, "draft"},
		{"checks fail before a review is asked", ghPull{State: "OPEN", ReviewDecision: "CHANGES_REQUESTED", Checks: failing("FAILURE")}, "checks_failing"},
		{"changes", ghPull{State: "OPEN", ReviewDecision: "CHANGES_REQUESTED"}, "changes_requested"},
		{"review required", ghPull{State: "OPEN", ReviewDecision: "REVIEW_REQUIRED"}, "waiting_review"},
		{"approved", ghPull{State: "OPEN", ReviewDecision: "APPROVED"}, "ready"},
		{"no review rule", ghPull{State: "OPEN"}, "ready"},
	} {
		if got := pullVerdict(c.pr)["verdict"]; got != c.want {
			t.Errorf("%s: %v, want %s", c.name, got, c.want)
		}
	}
	got := pullVerdict(ghPull{State: "OPEN", Checks: failing("FAILURE", "TIMED_OUT", "SUCCESS")})
	if got["failing"] != 2 {
		t.Fatalf("failing count: %v", got)
	}
	if _, has := pullVerdict(ghPull{State: "OPEN"})["failing"]; has {
		t.Fatal("failing is only on checks_failing")
	}
}

// gitIn runs git in a directory as a test author.
func gitIn(t *testing.T) func(dir string, args ...string) {
	return func(dir string, args ...string) {
		t.Helper()
		if out, err := exec.Command("git", append([]string{"-C", dir, "-c", "user.name=t", "-c", "user.email=t@t"}, args...)...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v %s", args, err, out)
		}
	}
}
