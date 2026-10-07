package daemon

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/lock"
)

func startAt(t *testing.T, dir string, own bool) (*Daemon, error) {
	t.Helper()
	d, err := Start(Config{Home: dir, DBFile: filepath.Join(dir, "db.sqlite"), Auth: fakeAuth{}, OwnPrompts: own})
	if err == nil {
		t.Cleanup(d.Close)
	}
	return d, err
}

func homeDir(t *testing.T) string {
	t.Helper()
	dir, err := os.MkdirTemp("", "hl")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	return dir
}

// A daemon started the way tests/daemon/test_go_reads.py starts one, beside a Python daemon's database, writes nothing to
// prompt_blocks: it neither watches screens nor holds the daemon singleton.
func TestADaemonStartedWithoutOwnPromptsNeitherWatchesNorTakesTheSingleton(t *testing.T) {
	dir := homeDir(t)
	python := lock.TakeSingleton(dir, daemonLock) // the Python daemon is up
	defer python.Release()
	d, err := startAt(t, dir, false)
	if err != nil {
		t.Fatal(err)
	}
	if d.single != nil {
		t.Fatal("it took the singleton")
	}
	d.db.Exec("INSERT INTO agent_state(agent, state, updated, terminal) VALUES('manager', 'IDLE', 0, '%1')")
	d.db.Exec("INSERT INTO prompt_blocks(agent, kind, fingerprint, title, panel, detail, answerable, first_seen, last_seen) VALUES('manager', 'question', 'f', 't', '[]', '{}', 1, 1, 1)")
	d.Close()
	var gone any
	d2, err := startAt(t, dir, false)
	if err != nil {
		t.Fatal(err)
	}
	d2.db.QueryRow("SELECT gone_at FROM prompt_blocks WHERE agent = 'manager'").Scan(&gone)
	if gone != nil {
		t.Fatalf("the open prompt row was closed: %v", gone)
	}
}

func TestTheScreenWatchRunsOnlyForTheDaemonThatTakesTheSingleton(t *testing.T) {
	dir := homeDir(t)
	python := lock.TakeSingleton(dir, daemonLock)
	if _, err := startAt(t, dir, true); err == nil || !strings.Contains(err.Error(), "another daemon holds this home") {
		t.Fatalf("it started beside a daemon that holds the home: %v", err)
	}
	python.Release()
	first, err := startAt(t, dir, true)
	if err != nil {
		t.Fatal(err)
	}
	if first.single == nil {
		t.Fatal("the watching daemon holds no singleton")
	}
	if _, err := startAt(t, dir, true); err == nil {
		t.Fatal("two watching daemons on one home")
	}
	first.Close()
	if second, err := startAt(t, dir, true); err != nil || second.single == nil {
		t.Fatalf("the singleton is not released on Close: %v", err)
	}
}
