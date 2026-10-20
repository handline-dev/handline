package worktreeprune

import (
	"os"
	"path/filepath"
	"testing"

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
