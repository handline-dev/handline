package housekeeping

import (
	"database/sql"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/store"
)

// diskWorld is a watch over a fake volume whose free space the test sets; told counts the disk_low events it wrote.
func diskWorld(t *testing.T) (*DiskWatch, *int64, func() []string, *sql.DB) {
	t.Helper()
	d, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	free := new(int64)
	w := &DiskWatch{Home: t.TempDir(), Log: func(string) {},
		Free: func(string) (int64, int64, error) { return *free, 460 * GB, nil }}
	told := func() []string {
		rows, err := d.Query("SELECT payload FROM events WHERE kind = ? ORDER BY id", DiskLowEvent)
		if err != nil {
			t.Fatal(err)
		}
		defer rows.Close()
		var out []string
		for rows.Next() {
			var p string
			rows.Scan(&p)
			out = append(out, p)
		}
		return out
	}
	return w, free, told, d
}

// Disk low is told as one notification event, never a task: no task or request is made.
func TestOneNotificationUnderTenGBAndNoneAgainUntilItRecovers(t *testing.T) {
	w, free, told, d := diskWorld(t)
	now := int64(1_000_000)
	at := func(gb float64) int {
		t.Helper()
		*free = int64(gb * float64(GB))
		now += DiskEvery
		if err := w.Check(d, now); err != nil {
			t.Fatal(err)
		}
		return len(told())
	}
	if at(25) != 0 || at(10) != 0 {
		t.Fatalf("at or above 10 GB: %q", told())
	}
	if at(9.5) != 1 || !strings.Contains(told()[0], "total_bytes") {
		t.Fatalf("under 10 GB: %q", told())
	}
	if at(4) != 1 || at(1) != 1 {
		t.Fatalf("no repeat while it stays low: %q", told())
	}
	if at(11) != 1 || at(9) != 1 {
		t.Fatalf("back just over the line is not recovered: %q", told())
	}
	if at(12) != 1 || at(9) != 2 {
		t.Fatalf("recovered, then under again: %q", told())
	}
	var tasks int
	if err := d.QueryRow("SELECT COUNT(*) FROM tasks").Scan(&tasks); err != nil {
		t.Fatal(err)
	}
	if tasks != 0 {
		t.Fatalf("a low disk makes no task: %d", tasks)
	}
}

func TestTheDiskIsCheckedAtMostEveryTenMinutesAndAToldLevelOutlivesARestart(t *testing.T) {
	w, free, told, d := diskWorld(t)
	*free = 30 * GB
	w.Check(d, 1000)
	*free = 5 * GB
	w.Check(d, 1000+DiskEvery-1)
	if len(told()) != 0 {
		t.Fatalf("checked too soon: %q", told())
	}
	w.Check(d, 1000+DiskEvery)
	restarted := &DiskWatch{Home: w.Home, Log: w.Log, Free: w.Free}
	restarted.Check(d, 1000+2*DiskEvery)
	if len(told()) != 1 {
		t.Fatalf("told once across a restart: %q", told())
	}
}

func TestTheWatchRemovesNothing(t *testing.T) {
	w, free, told, d := diskWorld(t)
	f := filepath.Join(w.Home, "agents", "x")
	os.MkdirAll(filepath.Dir(f), 0o755)
	os.WriteFile(f, []byte("x"), 0o644)
	*free = 1 * GB
	if err := w.Check(d, 1); err != nil || len(told()) != 1 {
		t.Fatalf("%v %q", err, told())
	}
	if _, err := os.Stat(f); err != nil {
		t.Fatalf("%s went: %v", f, err)
	}
}
