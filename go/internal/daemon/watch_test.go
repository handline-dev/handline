package daemon

import (
	"database/sql"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/watcher"
)

// taskRows is how many task.changed rows the daemon's stream holds after a task is made and then changed.
func taskRows(t *testing.T, watch bool) int {
	t.Helper()
	dir, err := os.MkdirTemp("", "hl")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(dir)
	d, err := Start(Config{Home: dir, DBFile: filepath.Join(dir, "db.sqlite"), SocketFile: filepath.Join(dir, "d.sock"), Auth: fakeAuth{}, Watch: watch})
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	time.Sleep(3 * watcher.Every) // the first look
	if _, err := d.db.Exec("INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 't', 'bob', 'human', 1)"); err != nil {
		t.Fatal(err)
	}
	time.Sleep(4 * watcher.Every)
	var n int
	if err := d.db.QueryRow("SELECT COUNT(*) FROM stream_log WHERE topic = 'tasks' AND type = 'task.changed'").Scan(&n); err != nil && err != sql.ErrNoRows {
		t.Fatal(err)
	}
	return n
}

func TestTheWatcherIsOffUntilCutoverAndThenOneChangeIsOneRow(t *testing.T) {
	if n := taskRows(t, false); n != 0 {
		t.Fatalf("the watcher was off and wrote %d rows", n)
	}
	if n := taskRows(t, true); n != 1 {
		t.Fatalf("one change wrote %d rows", n)
	}
}
