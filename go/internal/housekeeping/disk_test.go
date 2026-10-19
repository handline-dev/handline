package housekeeping

import (
	"database/sql"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	_ "modernc.org/sqlite"
)

// diskWorld is a watch over a fake volume whose free space the test sets, and the notices it sent.
func diskWorld(t *testing.T) (*DiskWatch, *int64, *[]string, *sql.DB) {
	t.Helper()
	d, err := sql.Open("sqlite", "file:"+filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	if _, err := d.Exec("CREATE TABLE cursors(name TEXT PRIMARY KEY, row_id INTEGER, updated INTEGER); " +
		"CREATE TABLE agent_state(agent TEXT PRIMARY KEY, extra_worktrees TEXT)"); err != nil {
		t.Fatal(err)
	}
	free, told := new(int64), &[]string{}
	w := &DiskWatch{Home: t.TempDir(), Log: func(string) {},
		Free: func(string) (int64, int64, error) { return *free, 460 * GB, nil }}
	w.Raise = func(_ db.Q, text string) error { *told = append(*told, text); return nil }
	return w, free, told, d
}

func TestOneCardUnderTenGBAndNoneAgainUntilItRecovers(t *testing.T) {
	w, free, told, d := diskWorld(t)
	now := int64(1_000_000)
	at := func(gb float64) int {
		t.Helper()
		*free = int64(gb * float64(GB))
		now += DiskEvery
		if err := w.Check(d, now); err != nil {
			t.Fatal(err)
		}
		return len(*told)
	}
	if at(25) != 0 || at(10) != 0 {
		t.Fatalf("at or above 10 GB: %q", *told)
	}
	if at(9.5) != 1 || !strings.HasPrefix((*told)[0], "disk 9 GB left (of 460 GB") {
		t.Fatalf("under 10 GB: %q", *told)
	}
	if at(4) != 1 || at(1) != 1 {
		t.Fatalf("no repeat while it stays low: %q", *told)
	}
	if at(11) != 1 || at(9) != 1 {
		t.Fatalf("back just over the line is not recovered: %q", *told)
	}
	if at(12) != 1 || at(9) != 2 {
		t.Fatalf("recovered, then under again: %q", *told)
	}
}

func TestTheCardNamesWhatCanBeCleared(t *testing.T) {
	w, free, told, d := diskWorld(t)
	tmp := t.TempDir()
	t.Setenv("TMPDIR", tmp)
	t.Setenv("PNPM_HOME", "/pnpm")
	for _, name := range []string{"TestOld", "handline-old", "TestNew", "other"} {
		os.Mkdir(filepath.Join(tmp, name), 0o755)
	}
	day := time.Now().Add(-25 * time.Hour)
	os.Chtimes(filepath.Join(tmp, "TestOld"), day, day)
	os.Chtimes(filepath.Join(tmp, "handline-old"), day, day)
	os.Chtimes(filepath.Join(tmp, "other"), day, day)
	wt := t.TempDir()
	d.Exec("INSERT INTO agent_state(agent, extra_worktrees) VALUES('a', ?), ('b', '[]')", `["`+wt+`", "/no/such/worktree"]`)
	*free = 5 * GB
	if err := w.Check(d, time.Now().Unix()); err != nil || len(*told) != 1 {
		t.Fatalf("%v %q", err, *told)
	}
	card := (*told)[0]
	for _, want := range []string{"/pnpm/store", "2 test temp dirs older than a day", "past task: " + wt} {
		if !strings.Contains(card, want) {
			t.Errorf("no %q in %q", want, card)
		}
	}
	if strings.Contains(card, "/no/such/worktree") {
		t.Errorf("a worktree that is gone is not named: %q", card)
	}
}

func TestTheDiskIsCheckedAtMostEveryTenMinutesAndAToldLevelOutlivesARestart(t *testing.T) {
	w, free, told, d := diskWorld(t)
	*free = 30 * GB
	w.Check(d, 1000)
	*free = 5 * GB
	w.Check(d, 1000+DiskEvery-1)
	if len(*told) != 0 {
		t.Fatalf("checked too soon: %q", *told)
	}
	w.Check(d, 1000+DiskEvery)
	restarted := &DiskWatch{Home: w.Home, Raise: w.Raise, Log: w.Log, Free: w.Free}
	restarted.Check(d, 1000+2*DiskEvery)
	if len(*told) != 1 {
		t.Fatalf("told once across a restart: %q", *told)
	}
}

func TestTheWatchRemovesNothing(t *testing.T) {
	w, free, told, d := diskWorld(t)
	cache := t.TempDir()
	t.Setenv("GOCACHE", cache)
	files := []string{filepath.Join(w.Home, "agents", "x"), filepath.Join(cache, "00", "entry")}
	for _, f := range files {
		os.MkdirAll(filepath.Dir(f), 0o755)
		os.WriteFile(f, []byte("x"), 0o644)
	}
	*free = 1 * GB
	if err := w.Check(d, 1); err != nil || len(*told) != 1 || !strings.Contains((*told)[0], cache) {
		t.Fatalf("%v %q", err, *told)
	}
	for _, f := range files {
		if _, err := os.Stat(f); err != nil {
			t.Fatalf("%s went: %v", f, err)
		}
	}
}
