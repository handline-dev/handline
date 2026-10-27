package lifecycle

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/gitrun"
	"github.com/handline/handline/go/internal/peer"
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
	// While the session that locked it still runs, it is not made again under it.
	lockAs := func(pid int, started int64) {
		gitrun.Run(repo, "worktree", "unlock", wt)
		gitrun.Run(repo, "worktree", "lock", "--reason", fmt.Sprintf("handline: shop.sea runs here (pid %d, started %d)", pid, started), wt)
	}
	live, _ := peer.Started(os.Getpid())
	lockAs(os.Getpid(), live.Unix())
	if path, why := s.Recreate("shop.sea", repo, wt); path != "" || !strings.Contains(why, "session that locked it still runs") {
		t.Fatalf("made again under a live session: %q %q", path, why)
	}
	lockAs(999999, 1)
	if path, why := s.Recreate("shop.sea", repo, "repo-id::"+wt); path != wt || why != "" || WorktreePath(wt) != wt {
		t.Fatalf("not made again: %q %q", path, why)
	}
	gone := filepath.Join(root, "proj.worktrees", "shop.air")
	if path, why := s.Recreate("shop.air", repo, gone); path != "" || !strings.Contains(why, "its branch shop.air is not in") {
		t.Fatalf("a worktree whose branch is gone: %q %q", path, why)
	}
	os.RemoveAll(wt)
	for _, agent := range []string{"shop", "shop.se"} { // shop.sea is no spawn branch of either
		if path, why := s.Recreate(agent, repo, wt); path != "" || !strings.Contains(why, "not one a spawn made for it") {
			t.Fatalf("%s took another agent's worktree and branch: %q %q", agent, path, why)
		}
	}
	if !spawnBranch("needsyou", "needsyou-2") || spawnBranch("needsyou", "needsyou-probe") || spawnBranch("needsyou", "needsyou-") {
		t.Fatal("a spawn branch is the name or <name>-<digits>")
	}
	if path, why := s.Recreate("x", repo, "orcapipe/handline"); path != "" || !strings.Contains(why, "not an absolute path") || WorktreePath(".") != "" {
		t.Fatalf("a relative path: %q %q", path, why)
	}
}

func TestWorktreesDirIsNoindexAndTheOldNameIsStillOurs(t *testing.T) {
	repo := "/w/proj"
	if got := WorktreesDir(repo); got != "/w/proj.worktrees.noindex" {
		t.Fatalf("%q", got)
	}
	for dir, want := range map[string]bool{"/w/proj.worktrees.noindex": true, "/w/proj.worktrees": true, "/w/other.worktrees.noindex": false, "/w/proj": false} {
		if inWorktreesDir(dir, repo) != want {
			t.Fatalf("%s: want %v", dir, want)
		}
	}
}
