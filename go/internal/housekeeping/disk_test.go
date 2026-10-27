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

// Under the floor the founder has one needs-you item, its figure kept current and never repeated; it goes once space is back over the
// floor plus the margin, and a volume that cannot be read raises nothing.
func TestOneNeedsYouItemWhileTheDiskIsUnderTheFloor(t *testing.T) {
	w, free, _, d := diskWorld(t)
	now := int64(1_000_000)
	check := func(gb float64) (int64, int64, bool) {
		t.Helper()
		*free = int64(gb * float64(GB))
		now += DiskEvery
		if err := w.Check(d, now); err != nil {
			t.Fatal(err)
		}
		return DiskNeed(d)
	}
	if _, _, ok := check(5); ok {
		t.Fatal("an item at the floor itself")
	}
	under := 4.9
	since, f, ok := check(under)
	if !ok || f != int64(under*float64(GB)) {
		t.Fatalf("under the floor: %v %d", ok, f)
	}
	if s2, f2, _ := check(3); s2 != since || f2 != 3*GB {
		t.Fatalf("a second check raised it again or kept the old figure: since %d->%d free %d", since, s2, f2)
	}
	if _, _, ok := check(6.9); !ok {
		t.Fatal("it went before the margin")
	}
	if _, _, ok := check(7); ok {
		t.Fatal("it stays over floor plus margin")
	}
	if _, _, ok := check(4); !ok {
		t.Fatal("it did not come back")
	}
	broken := &DiskWatch{Home: w.Home, Log: func(string) {}, Free: func(string) (int64, int64, error) { return 0, 0, os.ErrPermission }}
	d2 := openTemp(t)
	if err := broken.Check(d2, now); err == nil {
		t.Fatal("a statfs error was swallowed")
	}
	if _, _, ok := DiskNeed(d2); ok {
		t.Fatal("a statfs error raised the item")
	}
}

func TestTheFloorIsTheConfiguredOne(t *testing.T) {
	w, free, _, d := diskWorld(t)
	w.Floor = func() int64 { return 20 * GB }
	*free = 15 * GB
	if err := w.Check(d, 1_000_000); err != nil {
		t.Fatal(err)
	}
	if _, _, ok := DiskNeed(d); !ok {
		t.Fatal("15 GB free is under a floor of 20")
	}
}

func TestTheNeedIsInPlainWords(t *testing.T) {
	for free, want := range map[int64]string{3 * GB: "(3.0 GB free)", GB / 2: "(512 MB free)"} {
		if got := DiskNeedText(free); !strings.Contains(got, want) || !strings.HasSuffix(got, "wait until space is freed.") {
			t.Errorf("%q", got)
		}
	}
}

func openTemp(t *testing.T) *sql.DB {
	t.Helper()
	d, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	return d
}
