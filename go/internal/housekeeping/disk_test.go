package housekeeping

import (
	"database/sql"
	"os"
	"path/filepath"
	"strings"
	"testing"

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
	if _, err := d.Exec("CREATE TABLE cursors(name TEXT PRIMARY KEY, row_id INTEGER, updated INTEGER)"); err != nil {
		t.Fatal(err)
	}
	free, told := new(int64), &[]string{}
	w := &DiskWatch{Home: t.TempDir(), Log: func(string) {},
		Free: func(string) (int64, int64, error) { return *free, 460 * GB, nil }}
	w.Tell = func(_ db.Q, text string) error { *told = append(*told, text); return nil }
	return w, free, told, d
}

func TestTheFounderIsToldUnderTheLineAndAgainOnlyAStepLower(t *testing.T) {
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
	if at(25) != 0 || at(20) != 0 {
		t.Fatalf("at or above 20 GB: %q", *told)
	}
	if at(19.5) != 1 || !strings.Contains((*told)[0], "19 GB free of 460 GB") || !strings.Contains((*told)[0], "under 15 GB") {
		t.Fatalf("under 20 GB: %q", *told)
	}
	if at(16) != 1 || at(15) != 1 {
		t.Fatalf("not a step further: %q", *told)
	}
	if at(14.9) != 2 || at(17) != 2 || at(12) != 2 {
		t.Fatalf("a step further, then within it: %q", *told)
	}
	if at(4) != 3 || strings.Contains((*told)[2], "next notice") {
		t.Fatalf("two steps at once is one notice, and none below is promised past 0: %q", *told)
	}
	// Back near the line is not recovered; back at 25 GB is, and a new fall is told again.
	if at(22) != 3 || at(19) != 3 {
		t.Fatalf("hovering at the line: %q", *told)
	}
	if at(25) != 3 || at(19) != 4 {
		t.Fatalf("recovered, then under again: %q", *told)
	}
}

func TestTheDiskIsCheckedAtMostEveryTenMinutesAndAToldLevelOutlivesARestart(t *testing.T) {
	w, free, told, d := diskWorld(t)
	*free = 30 * GB
	w.Check(d, 1000)
	*free = 10 * GB
	w.Check(d, 1000+DiskEvery-1)
	if len(*told) != 0 {
		t.Fatalf("checked too soon: %q", *told)
	}
	w.Check(d, 1000+DiskEvery)
	restarted := &DiskWatch{Home: w.Home, Tell: w.Tell, Log: w.Log, Free: w.Free}
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
