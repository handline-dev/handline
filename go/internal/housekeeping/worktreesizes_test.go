package housekeeping

import (
	"path/filepath"
	"strconv"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/worktreeprune"
)

// A walk's sizes are written by the next check, replacing the last walk's (a worktree no longer an orphan loses its row), and a walk
// starts at most every SizeEvery.
func TestOrphanSizesAreKeptFromTheLastWalk(t *testing.T) {
	q, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer q.Close()
	walks := 0
	sizes := map[string]int64{}
	db.SetMeta(q, worktreeprune.SizePrefix+"/gone", "7")
	w := &WorktreeSizes{Log: func(string) {}, Size: func(p string) (int64, error) { return sizes[p], nil }}
	w.Orphans = func(db.Q) ([]string, error) { walks++; return []string{"/a"}, nil }
	sizes["/a"] = 4096
	if err := w.Check(q, 1000); err != nil {
		t.Fatal(err)
	}
	waitIdle(t, w)
	w.Check(q, 1001)
	if v, _, _ := db.Meta(q, worktreeprune.SizePrefix+"/a"); v != "4096" {
		t.Fatalf("size of /a: %q", v)
	}
	if _, ok, _ := db.Meta(q, worktreeprune.SizePrefix+"/gone"); ok {
		t.Fatal("a worktree no longer an orphan keeps its size")
	}
	if walks != 1 {
		t.Fatalf("%d walks within the hour", walks)
	}
	sizes["/a"] = 8192
	w.Check(q, 1000+worktreeprune.SizeEvery)
	waitIdle(t, w)
	w.Check(q, 1001+worktreeprune.SizeEvery)
	if v, _, _ := db.Meta(q, worktreeprune.SizePrefix+"/a"); v != strconv.Itoa(8192) || walks != 2 {
		t.Fatalf("after the hour: %q, %d walks", v, walks)
	}
}

// A check while a walk still runs, though another is due, starts no second walk over the same trees.
func TestNoSecondWalkStartsWhileOneRuns(t *testing.T) {
	q, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer q.Close()
	walks, release := 0, make(chan struct{})
	w := &WorktreeSizes{Log: func(string) {}, Size: func(string) (int64, error) { <-release; return 1, nil }}
	w.Orphans = func(db.Q) ([]string, error) { walks++; return []string{"/a"}, nil }
	w.Check(q, 1000)
	w.Check(q, 1000+worktreeprune.SizeEvery)
	close(release)
	waitIdle(t, w)
	if walks != 1 {
		t.Fatalf("%d walks", walks)
	}
}

func waitIdle(t *testing.T, w *WorktreeSizes) {
	t.Helper()
	for i := 0; i < 500; i++ {
		w.mu.Lock()
		idle := !w.running
		w.mu.Unlock()
		if idle {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("the walk did not end")
}
