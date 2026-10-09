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

// watchedTypes are the event types the test counts, one source change each: a task, an agent view, a task-done event, a request and a
// proposal file.
var watchedTypes = []string{"task.changed", "agent.changed", "task_done", "chat.message", "hr.raised"}

// watchedRows is how many rows of each type the daemon's stream holds once the watch has had its first look and the changes are made.
func watchedRows(t *testing.T, watch bool) map[string]int {
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
			if d.db.QueryRow("SELECT COUNT(*) FROM cursors WHERE name = 'stream:chat_prompts'").Scan(&n); n > 0 {
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
		"INSERT INTO requests(id, ts, requester, target, body, state, kind) VALUES(1, 5, 'human', 'bob', 'hi', 'queued', 'say')",
	} {
		if _, err := d.db.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
	// a proposal file: the store is files, so no database commit tells of it
	os.MkdirAll(filepath.Join(dir, "hr"), 0o755)
	if err := os.WriteFile(filepath.Join(dir, "hr", "0000000a.yaml"),
		[]byte("id: \"0000000a\"\nkind: retire\nagent: old\nrequested_by: boss\nwhy: idle\ncreated: 5\nstate: proposed\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	time.Sleep(4 * watcher.Every)
	out := map[string]int{}
	for _, typ := range watchedTypes {
		var n int
		if err := d.db.QueryRow("SELECT COUNT(*) FROM stream_log WHERE type = ?", typ).Scan(&n); err != nil && err != sql.ErrNoRows {
			t.Fatal(err)
		}
		out[typ] = n
	}
	return out
}

func TestTheWatcherIsOffUntilCutoverAndThenOneChangeIsOneRow(t *testing.T) {
	for typ, n := range watchedRows(t, false) {
		if n != 0 {
			t.Errorf("the watcher was off and wrote %d %s rows", n, typ)
		}
	}
	for typ, n := range watchedRows(t, true) {
		if n != 1 {
			t.Errorf("one change wrote %d %s rows", n, typ)
		}
	}
}
