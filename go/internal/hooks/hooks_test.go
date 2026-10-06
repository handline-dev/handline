package hooks

import (
	"database/sql"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/store"
)

func testRecorder(t *testing.T) *Recorder {
	t.Helper()
	home := t.TempDir()
	db, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })
	return &Recorder{DB: db, Home: home, Log: func(string, ...any) {}}
}

func count(t *testing.T, db *sql.DB, q string, args ...any) int {
	t.Helper()
	var n int
	if err := db.QueryRow(q, args...).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

func TestAHookAppliesOnceByRequestIDAndALateOneMovesNothing(t *testing.T) {
	h := testRecorder(t)
	now := time.Now().Unix()
	apply := func(kind, rid string, at int64) {
		t.Helper()
		if err := Apply(h.DB, h.Home, "w", kind, map[string]any{}, rid, at); err != nil {
			t.Fatal(err)
		}
	}
	apply("UserPromptSubmit", "a", now-10)
	apply("UserPromptSubmit", "a", now-9) // the same id
	apply("Stop", "b", now-5)             // RUNNING -> IDLE
	apply("UserPromptSubmit", "c", now-8) // late: recorded, nothing moves
	if n := count(t, h.DB, "SELECT COUNT(*) FROM events WHERE agent = 'w'"); n != 3 {
		t.Errorf("%d events, want 3", n)
	}
	var state string
	h.DB.QueryRow("SELECT state FROM agent_state WHERE agent = 'w'").Scan(&state)
	if state != "IDLE" {
		t.Errorf("state %s: a late event moved it", state)
	}
}

func TestTheSpoolSetsABadLineAsideAndAppliesTheRest(t *testing.T) {
	h := testRecorder(t)
	now := time.Now().Unix()
	good := `{"agent": "w", "kind": "Stop", "at": ` + itoa(now-5) + `, "data": {}, "request_id": "g1"}`
	os.WriteFile(spoolPath(h.Home), []byte("garbage\n"+good+"\n"+good+"\n"), 0o600)
	n, err := h.Ingest()
	if err != nil || n != 2 {
		t.Fatalf("applied %d, %v", n, err)
	}
	if c := count(t, h.DB, "SELECT COUNT(*) FROM events WHERE agent = 'w'"); c != 1 {
		t.Errorf("%d events, want 1: a duplicate id applies once", c)
	}
	rej, _ := os.ReadFile(filepath.Join(h.Home, rejectedName))
	if !strings.Contains(string(rej), "garbage") {
		t.Errorf("rejected: %s", rej)
	}
	if _, err := os.Stat(ingesting(h.Home)); err == nil {
		t.Error("the ingest file was not removed")
	}
}

func TestAnIngestThatFailsKeepsTheLinesItDidNotApply(t *testing.T) {
	h := testRecorder(t)
	line := func(id string) string {
		return `{"agent": "w", "kind": "Stop", "at": 1, "data": {}, "request_id": "` + id + `"}`
	}
	os.WriteFile(ingesting(h.Home), []byte(line("k1")+"\n"+line("k2")+"\n"), 0o600)
	h.DB.Close() // every apply now fails: not the lines' fault
	if _, err := h.Ingest(); err == nil {
		t.Fatal("no error from a closed database")
	}
	b, _ := os.ReadFile(ingesting(h.Home))
	if !strings.Contains(string(b), "k1") || !strings.Contains(string(b), "k2") {
		t.Errorf("lines were lost: %s", b)
	}
}

func itoa(n int64) string { return strconv.FormatInt(n, 10) }
