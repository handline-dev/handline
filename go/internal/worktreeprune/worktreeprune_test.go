package worktreeprune

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/gitrun"
)

// repo is a repository with main and one worktree per case, named for it.
func repo(t *testing.T) (string, map[string]string) {
	t.Helper()
	return repoOn(t, "main")
}

// repoOn is repo with branch for main.
func repoOn(t *testing.T, branch string) (string, map[string]string) {
	t.Helper()
	root := Real(t.TempDir())
	main := filepath.Join(root, "main")
	git := func(dir string, args ...string) {
		t.Helper()
		if r := gitrun.Run(dir, args...); r.RC != 0 {
			t.Fatalf("git %v: %s", args, r.Stderr)
		}
	}
	os.MkdirAll(main, 0o755)
	git(main, "init", "-q", "-b", branch)
	git(main, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "start")
	wts := map[string]string{}
	for _, name := range []string{"merged", "ahead", "untracked", "locked", "agents"} {
		wts[name] = filepath.Join(root, name)
		git(main, "worktree", "add", "-q", "-b", name, wts[name])
	}
	git(wts["ahead"], "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "unmerged work")
	os.WriteFile(filepath.Join(wts["untracked"], "new.txt"), []byte("x"), 0o644)
	return main, wts
}

func TestEachWorktreeIsKeptForItsReasonAndOnlyADoneOneGoes(t *testing.T) {
	main, wts := repo(t)
	gitrun.Run(main, "worktree", "lock", wts["locked"])
	agents := map[string]bool{Real(wts["agents"]): true}
	got := map[string]string{}
	for _, wt := range List(main) {
		got[filepath.Base(wt.Path)] = WhyKept(wt, agents, main)
	}
	for name, want := range map[string]string{
		"main":      "this is run from it",
		"merged":    "",
		"ahead":     "ahead is not merged into main",
		"untracked": "uncommitted or untracked files",
		"locked":    "locked",
		"agents":    "an agent works in it",
	} {
		if got[name] != want {
			t.Errorf("%s: %q, want %q", name, got[name], want)
		}
	}
	if ok, why := Remove(wts["merged"]); !ok {
		t.Fatal(why)
	}
	if _, err := os.Stat(wts["merged"]); !os.IsNotExist(err) {
		t.Fatal("the worktree is still there")
	}
	if ok, _ := Remove(wts["untracked"]); ok {
		t.Fatal("a worktree with an untracked file was removed")
	}
}

func TestHoldsWorkIsTheTwoReasonsAPersonMustJudge(t *testing.T) {
	for why, want := range map[string]bool{"ahead is not merged into main": true, "uncommitted or untracked files": true, "locked": false, "an agent works in it": false, "main": false, "": false} {
		if HoldsWork(why) != want {
			t.Errorf("%q: %v", why, !want)
		}
	}
}

func TestDescribeListsTheCommitsAndFilesThatWouldBeLost(t *testing.T) {
	main, wts := repo(t)
	for _, wt := range List(main) {
		if filepath.Base(wt.Path) == "ahead" {
			if d := Describe(wt); !strings.Contains(d, "Commits not on main:\n  unmerged work") || !strings.Contains(d, "Branch: ahead") {
				t.Fatalf("%s", d)
			}
		}
		if filepath.Base(wt.Path) == "untracked" {
			if d := Describe(wt); !strings.Contains(d, "?? new.txt") {
				t.Fatalf("%s", d)
			}
		}
	}
	_ = wts
}

func TestRescueCommitsWhatIsLeftOnABranchThenRemovesTheWorktree(t *testing.T) {
	main, wts := repo(t)
	day := time.Date(2026, 10, 26, 0, 0, 0, 0, time.UTC)
	branch, err := Rescue(wts["untracked"], "ann", day)
	if err != nil || branch != "rescue/ann-2026-10-26" {
		t.Fatalf("%q %v", branch, err)
	}
	if _, err := os.Stat(wts["untracked"]); !os.IsNotExist(err) {
		t.Fatal("the worktree is still there")
	}
	if r := gitrun.Run(main, "show", branch+":new.txt"); r.RC != 0 || r.Stdout != "x" {
		t.Fatalf("the file is not on the rescue branch: %+v", r)
	}
	// A second rescue the same day takes the next name.
	gitrun.Run(main, "worktree", "add", "-q", "-b", "again", filepath.Join(filepath.Dir(main), "again"))
	if b, err := Rescue(filepath.Join(filepath.Dir(main), "again"), "ann", day); err != nil || b != "rescue/ann-2026-10-26-2" {
		t.Fatalf("%q %v", b, err)
	}
}

func TestRemoveForceTakesADirtyWorktreeAndKeepsItsBranch(t *testing.T) {
	main, wts := repo(t)
	if ok, why := RemoveForce(wts["untracked"]); !ok {
		t.Fatal(why)
	}
	if r := gitrun.Run(main, "rev-parse", "--verify", "untracked"); r.RC != 0 {
		t.Fatal("the branch went with it")
	}
}

// A repository whose default branch is not main is measured against that branch, as origin's HEAD names it.
func TestUnsavedCountsAgainstTheRepositorysDefaultBranch(t *testing.T) {
	main, wts := repoOn(t, "trunk")
	gitrun.Run(main, "update-ref", "refs/remotes/origin/trunk", "trunk")
	gitrun.Run(main, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
	if b := DefaultBranch(wts["ahead"]); b != "trunk" {
		t.Fatalf("default branch %q", b)
	}
	for name, want := range map[string][3]any{"merged": {true, 0, 0}, "ahead": {false, 0, 1}} {
		merged, files, commits := Unsaved(wts[name])
		if got := [3]any{merged, files, commits}; got != want {
			t.Errorf("%s: merged, files, commits %v, want %v", name, got, want)
		}
	}
}

// Size is bytes, at least what the files hold.
func TestSizeIsTheBytesUnderThePath(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "f"), make([]byte, 64<<10), 0o644); err != nil {
		t.Fatal(err)
	}
	if n, err := Size(dir); err != nil || n < 64<<10 || n > 1<<20 {
		t.Fatalf("size %d %v, want 64 KiB or a little more", n, err)
	}
}
