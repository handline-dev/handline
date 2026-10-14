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

func TestOnlyAFreshSessionStartNamesTheLaunchItCameFrom(t *testing.T) {
	h := testRecorder(t)
	now := time.Now().Unix()
	start := func(source, sid string) {
		t.Helper()
		if err := Apply(h.DB, h.Home, "w", "SessionStart", map[string]any{"source": source, "session_id": sid}, sid, now); err != nil {
			t.Fatal(err)
		}
	}
	named := func() string {
		var sid sql.NullString
		h.DB.QueryRow("SELECT session_id FROM session_starts WHERE agent = 'w' ORDER BY id LIMIT 1").Scan(&sid)
		return sid.String
	}
	if _, err := h.DB.Exec("INSERT INTO session_starts (agent, ts, cause, resumed, text, prompt) VALUES ('w', ?, 'wake', 0, '', '')", now); err != nil {
		t.Fatal(err)
	}
	start("clear", "s-clear")
	if got := named(); got != "" {
		t.Errorf("a /clear claimed the launch: %q", got)
	}
	start("startup", "s-new")
	if got := named(); got != "s-new" {
		t.Errorf("the new session did not name its launch: %q", got)
	}
	if _, err := h.DB.Exec("INSERT INTO session_starts (agent, ts, cause, resumed, text, prompt) VALUES ('w', ?, 'launch', 0, '', '')", now-7200); err != nil {
		t.Fatal(err)
	}
	start("startup", "s-late")
	if n := count(t, h.DB, "SELECT COUNT(*) FROM session_starts WHERE session_id = 's-late'"); n != 0 {
		t.Errorf("a launch too old was claimed")
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
