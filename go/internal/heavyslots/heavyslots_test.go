package heavyslots

import (
	"os"
	"path/filepath"
	"syscall"
	"testing"

	"github.com/handline/handline/go/internal/usage"
)

func TestTheConfiguredSlotsAreUsedAndAnythingElseIsTheDefault(t *testing.T) {
	for _, c := range []struct {
		v    any
		want int
	}{{3, 3}, {int64(4), 4}, {1, 1}, {0, Default}, {-2, Default}, {"3", Default}, {nil, Default}} {
		if got := Of(usage.Config{"heavy_slots": c.v}); got != c.want {
			t.Errorf("%v: %d, want %d", c.v, got, c.want)
		}
	}
}

func TestTheEnvironmentOverridesTheConfiguredSlots(t *testing.T) {
	t.Setenv("HANDLINE_HEAVY_SLOTS", "5")
	if Slots() != 5 {
		t.Fatalf("%d", Slots())
	}
}

func TestHeldCountsTheHeavySlotsSomeoneHolds(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("HANDLINE_HEAVY_DIR", filepath.Join(dir, "none"))
	if Held() != 0 {
		t.Fatal("a missing directory is 0")
	}
	t.Setenv("HANDLINE_HEAVY_DIR", dir)
	for _, name := range []string{"slot-1.lock", "slot-2.lock", "light-1.lock"} {
		if err := os.WriteFile(filepath.Join(dir, name), nil, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	hold := func(name string) {
		f, err := os.OpenFile(filepath.Join(dir, name), os.O_RDWR, 0)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { f.Close() })
		if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
			t.Fatal(err)
		}
	}
	if Held() != 0 {
		t.Fatal("no slot is held")
	}
	hold("slot-1.lock")
	hold("light-1.lock")
	if n := Held(); n != 1 {
		t.Fatalf("one heavy slot is held, the light one is not counted: %d", n)
	}
	if _, err := os.Stat(filepath.Join(dir, "slot-3.lock")); !os.IsNotExist(err) {
		t.Fatal("the probe creates no lock file")
	}
}
