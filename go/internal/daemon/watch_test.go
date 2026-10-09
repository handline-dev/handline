package daemon

import (
	"database/sql"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/watcher"
)

// watchedRows is how many task.changed and agent.changed rows the daemon's stream holds after a task and an agent view are made.
func watchedRows(t *testing.T, watch bool) (tasks, agents int) {
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
	if _, err := d.db.Exec(`INSERT INTO agent_view(agent, view, updated) VALUES('bob', '{"name":"bob","state":"idle"}', 0)`); err != nil {
		t.Fatal(err)
	}
	time.Sleep(4 * watcher.Every)
	for topic, n := range map[string]*int{"task.changed": &tasks, "agent.changed": &agents} {
		if err := d.db.QueryRow("SELECT COUNT(*) FROM stream_log WHERE type = ?", topic).Scan(n); err != nil && err != sql.ErrNoRows {
			t.Fatal(err)
		}
	}
	return tasks, agents
}

func TestTheWatcherIsOffUntilCutoverAndThenOneChangeIsOneRow(t *testing.T) {
	if tasks, agents := watchedRows(t, false); tasks != 0 || agents != 0 {
		t.Fatalf("the watcher was off and wrote %d task rows and %d agent rows", tasks, agents)
	}
	if tasks, agents := watchedRows(t, true); tasks != 1 || agents != 1 {
		t.Fatalf("one change of each wrote %d task rows and %d agent rows", tasks, agents)
	}
}
