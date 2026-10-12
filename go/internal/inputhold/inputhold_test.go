package inputhold

import (
	"path/filepath"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/store"
)

func rig(t *testing.T) (*Store, *[]string, *time.Time) {
	t.Helper()
	t.Setenv("HANDLINE_TEST_ROOT", t.TempDir())
	d, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	now := time.Unix(1_000_000, 0)
	told := []string{}
	s := &Store{DB: d, IdleS: func() int64 { return 60 }, Now: func() time.Time { return now },
		Changed: func(agent string, h []map[string]any) { told = append(told, agent+":"+h[0]["name"].(string)) }}
	return s, &told, &now
}

func TestOneTakesAndAnotherIsRefusedUntilTheFirstReleasesOrGoesIdle(t *testing.T) {
	s, told, now := rig(t)
	if won, _ := s.Take("ann", "person", "pat"); !won {
		t.Fatal("the first take wins")
	}
	if won, _ := s.Take("ann", "person", "pat"); !won {
		t.Fatal("a holder takes again")
	}
	if won, _ := s.Take("ann", "device", "sam"); won {
		t.Fatal("two takes cannot both win")
	}
	*now = now.Add(61 * time.Second) // idle: the hold is gone when it is next read, and the loss is told like a take
	if won, _ := s.Take("ann", "device", "sam"); !won {
		t.Fatal("an idle hold is lost")
	}
	if got := len(*told); got != 4 { // take, take, the idle loss (the daemon), take
		t.Fatalf("told %v", *told)
	}
	if name, _ := s.HeldByOther("ann"); name != "sam" {
		t.Fatalf("held by %q", name)
	}
	s.Release("ann")
	if v, _ := s.View("ann"); v[0]["kind"] != "daemon" {
		t.Fatalf("view %v", v)
	}
}

func TestTypingKeepsAHoldAlive(t *testing.T) {
	s, _, now := rig(t)
	s.Take("ann", "person", "pat")
	*now = now.Add(50 * time.Second)
	s.Touch("ann", "pat")
	*now = now.Add(50 * time.Second)
	if r, _ := s.Holder("ann"); r == nil {
		t.Fatal("a hold typed on 50s ago is held")
	}
}
