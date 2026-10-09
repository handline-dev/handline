package daemon

import (
	"database/sql"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/watcher"
)

// watchedRows is how many task.changed, agent.changed and task_done rows the daemon's stream holds after a task, an agent view and a
// task-done event are made once the watch has had its first look.
func watchedRows(t *testing.T, watch bool) (tasks, agents, notes int) {
	t.Helper()
	dir, err := os.MkdirTemp("", "hl")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(dir)
	d, err := Start(Config{Home: dir, DBFile: filepath.Join(dir, "db.sqlite"), SocketFile: filepath.Join(dir, "d.sock"), Auth: fakeAuth{}, Watch: watch})
	if err != nil {
		if strings.Contains(err.Error(), "operation not permitted") {
			t.Skipf("this environment will not bind a socket: %v", err)
		}
		t.Fatal(err)
	}
	defer d.Close()
	if watch { // the last detector's first look sets its cursors
		for i := 0; i < 200; i++ {
			var n int
			if d.db.QueryRow("SELECT COUNT(*) FROM cursors WHERE name = 'stream:prompts'").Scan(&n); n > 0 {
				break
			}
			time.Sleep(20 * time.Millisecond)
		}
	} else {
		time.Sleep(3 * watcher.Every)
	}
	for _, q := range []string{
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 't', 'bob', 'human', 1)",
		`INSERT INTO agent_view(agent, view, updated) VALUES('bob', '{"name":"bob","state":"idle"}', 0)`,
		`INSERT INTO events(ts, agent, kind, payload) VALUES(1, 'bob', 'mark_task_done', '{"task_id": 1}')`,
	} {
		if _, err := d.db.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
	time.Sleep(4 * watcher.Every)
	for typ, n := range map[string]*int{"task.changed": &tasks, "agent.changed": &agents, "task_done": &notes} {
		if err := d.db.QueryRow("SELECT COUNT(*) FROM stream_log WHERE type = ?", typ).Scan(n); err != nil && err != sql.ErrNoRows {
			t.Fatal(err)
		}
	}
	return tasks, agents, notes
}

func TestTheWatcherIsOffUntilCutoverAndThenOneChangeIsOneRow(t *testing.T) {
	if tasks, agents, notes := watchedRows(t, false); tasks+agents+notes != 0 {
		t.Fatalf("the watcher was off and wrote %d task rows, %d agent rows, %d notifications", tasks, agents, notes)
	}
	if tasks, agents, notes := watchedRows(t, true); tasks != 1 || agents != 1 || notes != 1 {
		t.Fatalf("one change of each wrote %d task rows, %d agent rows, %d notifications", tasks, agents, notes)
	}
}
