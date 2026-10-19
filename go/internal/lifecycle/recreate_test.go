package lifecycle

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/gitrun"
	"github.com/handline/handline/go/internal/worktreelock"
)

// A spawn's worktree that is gone is added again at its path on its branch, the lock its gone session left let go first; one with its
// branch gone, one that is not the agent's own, or a relative recorded path is refused, saying why.
func TestRecreateMakesAGoneWorktreeAgainFromItsBranch(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	root := t.TempDir()
	repo := filepath.Join(root, "proj")
	os.Mkdir(repo, 0o755)
	for _, args := range [][]string{{"init", "-q", "-b", "main"}, {"-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "first"}} {
		if r := gitrun.Run(repo, args...); r.RC != 0 {
			t.Fatal(r.Stderr)
		}
	}
	wt := filepath.Join(root, "proj.worktrees", "shop.sea")
	gitrun.Run(repo, "worktree", "add", "-q", "-b", "shop.sea", wt)
	// Deleted while its session held it: the lock stays in the repository.
	if err := worktreelock.Hold(wt, "shop.sea", 999999, 1, func(int, int64) bool { return false }); err != nil {
		t.Fatal(err)
	}
	os.RemoveAll(wt)
	s := &Spawns{}
	if path, why := s.Recreate("shop.sea", repo, "repo-id::"+wt); path != wt || why != "" || WorktreePath(wt) != wt {
		t.Fatalf("not made again: %q %q", path, why)
	}
	gone := filepath.Join(root, "proj.worktrees", "shop.air")
	if path, why := s.Recreate("shop.air", repo, gone); path != "" || !strings.Contains(why, "its branch shop.air is not in") {
		t.Fatalf("a worktree whose branch is gone: %q %q", path, why)
	}
	os.RemoveAll(wt)
	if path, why := s.Recreate("shop", repo, wt); path != "" || !strings.Contains(why, "not one a spawn made for it") {
		t.Fatalf("another agent's worktree and branch: %q %q", path, why)
	}
	if path, why := s.Recreate("x", repo, "orcapipe/handline"); path != "" || !strings.Contains(why, "not an absolute path") || WorktreePath(".") != "" {
		t.Fatalf("a relative path: %q %q", path, why)
	}
}
