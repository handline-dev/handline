package lifecycle

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/gitrun"
)

// A worktree that is gone is added again at its path on its branch; with the branch gone it is refused with why; a relative recorded
// path is never read against the daemon's own directory.
func TestRecreateMakesAGoneWorktreeAgainFromItsBranch(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	repo := t.TempDir()
	for _, args := range [][]string{{"init", "-q", "-b", "main"}, {"-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "first"}} {
		if r := gitrun.Run(repo, args...); r.RC != 0 {
			t.Fatal(r.Stderr)
		}
	}
	wt := filepath.Join(t.TempDir(), "shop.sea")
	gitrun.Run(repo, "worktree", "add", "-q", "-b", "shop.sea", wt)
	os.RemoveAll(wt)
	s := &Spawns{}
	if path, why := s.Recreate("shop.sea", repo, "repo-id::"+wt); path != wt || why != "" || WorktreePath(wt) != wt {
		t.Fatalf("not made again: %q %q", path, why)
	}
	other := filepath.Join(t.TempDir(), "shop.air")
	if path, why := s.Recreate("shop.air", repo, other); path != "" || !strings.Contains(why, "its branch shop.air is not in") {
		t.Fatalf("a worktree whose branch is gone: %q %q", path, why)
	}
	if path, why := s.Recreate("x", repo, "orcapipe/handline"); path != "" || !strings.Contains(why, "not an absolute path") || WorktreePath(".") != "" {
		t.Fatalf("a relative path: %q %q", path, why)
	}
}
