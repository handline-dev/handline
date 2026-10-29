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
	root := Real(t.TempDir())
	main := filepath.Join(root, "main")
	git := func(dir string, args ...string) {
		t.Helper()
		if r := gitrun.Run(dir, args...); r.RC != 0 {
			t.Fatalf("git %v: %s", args, r.Stderr)
		}
	}
	os.MkdirAll(main, 0o755)
	git(main, "init", "-q", "-b", "main")
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

// An orphan is a worktree no agent records; the repo's own checkout and a locked one never are. Unsaved says what each holds.
func TestOrphansAreTheWorktreesNoAgentRecordsWithWhatTheyHold(t *testing.T) {
	main, wts := repo(t)
	gitrun.Run(main, "worktree", "lock", wts["locked"])
	got := map[string][3]int{}
	for _, wt := range Orphans(main, map[string]bool{Real(wts["agents"]): true}) {
		merged, files, commits := Unsaved(wt.Path)
		m := 0
		if merged {
			m = 1
		}
		got[filepath.Base(wt.Path)] = [3]int{m, files, commits}
	}
	want := map[string][3]int{"merged": {1, 0, 0}, "ahead": {0, 0, 1}, "untracked": {1, 1, 0}}
	if len(got) != len(want) {
		t.Fatalf("orphans %v, want %v", got, want)
	}
	for name, w := range want {
		if got[name] != w {
			t.Errorf("%s: merged, files, commits %v, want %v", name, got[name], w)
		}
	}
}
