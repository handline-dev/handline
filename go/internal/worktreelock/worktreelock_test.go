package worktreelock

import (
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/gitrun"
)

func git(t *testing.T, dir string, args ...string) string {
	t.Helper()
	r := gitrun.Run(dir, args...)
	if r.RC != 0 {
		t.Fatalf("git %v: %s", args, r.Stderr)
	}
	return r.Stdout
}

// repo is a checkout with one linked worktree, the kind an agent runs in.
func repo(t *testing.T) (main, wt string) {
	t.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	main = t.TempDir()
	git(t, main, "init", "-q", "-b", "main")
	git(t, main, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "first")
	wt = filepath.Join(t.TempDir(), "wt")
	git(t, main, "worktree", "add", "-q", "-b", "agent", wt)
	return main, wt
}

func lockText(t *testing.T, wt string) string {
	_, _, _, why, err := lockOf(wt)
	if err != nil {
		t.Fatal(err)
	}
	return why
}

func TestARunningAgentsWorktreeIsLockedAndLetGoOnceItsProcessIsGone(t *testing.T) {
	main, wt := repo(t)
	running := map[int]bool{100: true}
	alive := func(pid int, started int64) bool { return running[pid] && started == 7 }
	if err := Hold(wt, "shop.sea", 100, 7, alive); err != nil {
		t.Fatal(err)
	}
	if got := lockText(t, wt); got != "handline: shop.sea runs here (pid 100, started 7)" {
		t.Fatalf("lock reason %q", got)
	}
	if r := gitrun.Run(main, "worktree", "remove", wt); r.RC == 0 {
		t.Fatal("a locked worktree was removed")
	}
	if err := Hold(wt, "shop.air", 200, 7, alive); err == nil || !strings.Contains(err.Error(), "still runs (pid 100)") {
		t.Fatalf("another session took a live one's lock: %v", err)
	}
	if done, err := Release(wt, alive); done || err != nil {
		t.Fatalf("let go while its session runs: %v %v", done, err)
	}
	running[100] = false
	if done, err := Release(wt, alive); !done || err != nil || lockText(t, wt) != "" {
		t.Fatalf("not let go once its process is gone: %v %v", done, err)
	}
	// A stale lock of ours is taken over by the next session; a person's own lock is never lifted.
	Hold(wt, "shop.sea", 100, 7, alive)
	if err := Hold(wt, "shop.sea", 300, 7, alive); err != nil || lockText(t, wt) != "handline: shop.sea runs here (pid 300, started 7)" {
		t.Fatalf("a stale lock was not taken over: %v", err)
	}
	git(t, main, "worktree", "unlock", wt)
	git(t, main, "worktree", "lock", "--reason", "keep: founder", wt)
	if done, _ := Release(wt, alive); done || lockText(t, wt) != "keep: founder" {
		t.Fatal("a person's lock was lifted")
	}
	// A pid given out again is another process: the lock it seems to hold is a gone session's.
	git(t, main, "worktree", "unlock", wt)
	running[100] = true
	Hold(wt, "shop.sea", 100, 6, alive)
	if done, _ := Release(wt, alive); !done {
		t.Fatal("a reused pid kept a gone session's lock")
	}
	if got := Linked(main); len(got) != 1 || canonical(got[0]) != canonical(wt) {
		t.Fatalf("linked worktrees: %v", got)
	}
	if err := Hold(main, "shop.sea", 100, 7, alive); err != nil {
		t.Fatalf("the main worktree is left as it is: %v", err)
	}
}
