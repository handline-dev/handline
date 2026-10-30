package reads

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

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
	if w["base"] != "main" || w["commits_ahead"] != 1 || w["files_changed"] != 1 || w["size_bytes"] != int64(2048) {
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

// The size is read under the worktree's real path, as housekeeping keeps it, though the agent records a path through a link.
// A worktree on its base, a detached one and one whose origin is not on GitHub are never asked about.
func TestWorktreesWithNoPullRequestToAskAboutAreNeverAsked(t *testing.T) {
	e := testEnv(t)
	e.BranchPR = func(_ context.Context, repo, branch string) (map[string]any, error) {
		t.Errorf("asked about %s %s", repo, branch)
		return nil, nil
	}
	root, _ := filepath.EvalSymlinks(t.TempDir())
	git := gitIn(t)
	repo, other, detached := filepath.Join(root, "repo"), filepath.Join(root, "other"), filepath.Join(root, "detached")
	for _, r := range []struct{ dir, origin string }{{repo, "git@github.com:acme/app.git"}, {other, "git@gitlab.com:acme/app.git"}} {
		os.MkdirAll(r.dir, 0o755)
		git(r.dir, "init", "-q", "-b", "main")
		git(r.dir, "remote", "add", "origin", r.origin)
		git(r.dir, "commit", "-q", "--allow-empty", "-m", "start")
	}
	git(repo, "worktree", "add", "-q", "--detach", detached)
	git(other, "checkout", "-q", "-b", "feature")
	link := filepath.Join(root, "link")
	if err := os.Symlink(repo, link); err != nil {
		t.Fatal(err)
	}
	for agent, path := range map[string]string{"onbase": link + "/", "detached": detached, "gitlab": other} {
		addAgent(t, e, agent, "")
		if _, err := e.DB.Exec("INSERT INTO agent_state(agent, state, updated, worktree) VALUES(?, 'idle', 0, ?)", agent, path); err != nil {
			t.Fatal(err)
		}
	}
	e.DB.Exec("INSERT INTO meta(name, value, updated) VALUES(?, '4096', 0)", "worktree_size:"+repo)
	got, err := e.worktreesList(nil, map[string]any{})
	if err != nil {
		t.Fatal(err)
	}
	rows := got.(map[string]any)["worktrees"].([]any)
	if len(rows) != 3 {
		t.Fatalf("%d rows", len(rows))
	}
	for _, r := range rows {
		w := r.(map[string]any)
		if w["pr"] != nil {
			t.Errorf("%v: pr %v", w["branch"], w["pr"])
		}
		if w["owner"] == "onbase" && w["size_bytes"] != int64(4096) {
			t.Errorf("a size kept under the real path is read for a linked one: %v", w["size_bytes"])
		}
	}
}

func TestAStatusContextOrAStartupFailureCountsAsFailing(t *testing.T) {
	pr := ghPull{State: "OPEN", Checks: []check{{State: "FAILURE"}, {State: "ERROR"}, {Conclusion: "STARTUP_FAILURE"}, {State: "SUCCESS"}, {Conclusion: "SUCCESS"}}}
	if got := pullVerdict(pr); got["verdict"] != "checks_failing" || got["failing"] != 3 {
		t.Fatalf("%v", got)
	}
}

func TestTheOpenPullRequestOfABranchStandsInFrontOfAMergedOne(t *testing.T) {
	pulls := []ghPull{{Number: 3, State: "MERGED"}, {Number: 2, State: "CLOSED"}, {Number: 1, State: "OPEN"}}
	if p, ok := chosenPull(pulls); !ok || p.Number != 1 {
		t.Fatalf("%v %v", p, ok)
	}
	if p, ok := chosenPull(pulls[:2]); !ok || p.Number != 3 {
		t.Fatalf("a merged one when none is open: %v %v", p, ok)
	}
	if _, ok := chosenPull(pulls[1:2]); ok {
		t.Fatal("a closed one is none")
	}
}

func TestABranchesPullRequestIsKeptAMinuteAndThenAskedAgain(t *testing.T) {
	now := time.Now()
	c := newBranchPRs()
	c.now = func() time.Time { return now }
	c.put("k", map[string]any{"number": 1})
	if _, hit := c.get("k"); !hit {
		t.Fatal("kept")
	}
	now = now.Add(branchPRTTL + time.Second)
	if _, hit := c.get("k"); hit {
		t.Fatal("kept past its minute")
	}
}
