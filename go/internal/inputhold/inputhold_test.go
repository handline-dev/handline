package inputhold

import (
	"database/sql"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/store"
)

func open(t *testing.T) (*sql.DB, string) {
	t.Helper()
	home := t.TempDir()
	db, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })
	return db, home
}

func hold(t *testing.T, db *sql.DB, agent, name string, lastTyped int64) {
	t.Helper()
	if _, err := db.Exec("INSERT INTO input_holders(agent, kind, name, since, last_typed) VALUES(?, 'person', ?, ?, ?)", agent, name, lastTyped, lastTyped); err != nil {
		t.Fatal(err)
	}
}

func TestAHoldIsReadBackUntilItsHolderHasBeenIdleLongEnough(t *testing.T) {
	db, home := open(t)
	now := time.Unix(10_000, 0)
	if h, _, err := Holder(db, home, "a.one", now); h != nil || err != nil {
		t.Fatalf("nobody holds it: %v %v", h, err)
	}
	hold(t, db, "a.one", "casey", now.Unix()-599)
	if h, _, _ := Holder(db, home, "a.one", now); h == nil || h.Name != "casey" || h.Kind != "person" {
		t.Fatalf("%+v", h)
	}
	if HeldWhy("a.one", "casey") != "a.one's terminal is held by casey" {
		t.Fatal(HeldWhy("a.one", "casey"))
	}
	// the tenth minute: the hold is dropped, and stays dropped
	if h, _, _ := Holder(db, home, "a.one", now.Add(time.Second)); h != nil {
		t.Fatalf("an idle hold stands: %+v", h)
	}
	var n int
	db.QueryRow("SELECT COUNT(*) FROM input_holders").Scan(&n)
	if n != 0 {
		t.Fatal("the idle hold is still a row")
	}
}

func TestTouchCountsTheIdleFromNowAndOnlyForTheHolder(t *testing.T) {
	db, home := open(t)
	now := time.Unix(10_000, 0)
	hold(t, db, "a.one", "casey", now.Unix()-500)
	Touch(db, "a.one", "someone.else", now)
	Touch(db, "a.one", "casey", now)
	var last int64
	db.QueryRow("SELECT last_typed FROM input_holders WHERE agent = 'a.one'").Scan(&last)
	if last != now.Unix() {
		t.Fatalf("last_typed %d", last)
	}
	if h, _, _ := Holder(db, home, "a.one", now.Add(599*time.Second)); h == nil {
		t.Fatal("the touch did not restart the idle count")
	}
}

func TestTheIdleLimitIsFromConfigIfAPositiveIntegerElseTheDefault(t *testing.T) {
	home := t.TempDir()
	if IdleSeconds(home) != 600 {
		t.Fatal("no config")
	}
	cfg := filepath.Join(home, "config.yaml")
	for text, want := range map[string]int64{"input_hold_idle_s: 30\n": 30, "input_hold_idle_s: 0\n": 600, "input_hold_idle_s: soon\n": 600, "x: [\n": 600} {
		os.WriteFile(cfg, []byte(text), 0o644)
		if got := IdleSeconds(home); got != want {
			t.Fatalf("%q -> %d, want %d", text, got, want)
		}
	}
}

func TestTakeWinsOnlyWhereNobodyElseHolds(t *testing.T) {
	db, home := open(t)
	now := time.Unix(10_000, 0)
	if ok, err := Take(db, "a.one", "person", "casey", now); !ok || err != nil {
		t.Fatalf("%v %v", ok, err)
	}
	if ok, _ := Take(db, "a.one", "person", "robin", now.Add(time.Second)); ok {
		t.Fatal("a second taker must lose")
	}
	if ok, _ := Take(db, "a.one", "person", "casey", now.Add(time.Second)); !ok {
		t.Fatal("the holder takes again")
	}
	if _, dropped, _ := Holder(db, home, "a.one", now.Add(time.Hour)); !dropped {
		t.Fatal("an idle hold is lost")
	}
	if ok, _ := Take(db, "a.one", "person", "robin", now.Add(time.Hour)); !ok {
		t.Fatal("an idle hold is lost")
	}
	if err := Release(db, "a.one"); err != nil {
		t.Fatal(err)
	}
	if h, _, _ := Holder(db, home, "a.one", now); h != nil {
		t.Fatal("released")
	}
}
