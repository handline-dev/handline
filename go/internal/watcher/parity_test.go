package watcher

import (
	"bufio"
	"encoding/json"
	osexec "os/exec"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/stream"
)

// The Python watcher's detector, run on a copy of the database the Go one watches: the same first look, the same change, and the rows
// it writes are the ones Go writes (the topic, type, agent, title, target and the Task in data).
const pythonTasks = `
import json, os, sqlite3, sys
sys.path.insert(0, os.getcwd())
from handline.daemon import stream
con = sqlite3.connect(sys.argv[1]); con.row_factory = sqlite3.Row
state = {}
stream._tasks(con, state)
print("ready", flush=True)
sys.stdin.readline()
stream._tasks(con, state); con.commit()
print(json.dumps([dict(r) for r in con.execute("SELECT topic, type, agent, title, target, data FROM stream_log ORDER BY id")]))
`

func TestGoAndPythonWriteTheSameRowsForTheSameTaskChange(t *testing.T) {
	root, _ := filepath.Abs("../../..")
	dir := t.TempDir()
	open := func(name string) (*stream.Log, *Tasks, func(string, ...any)) {
		d, err := store.Open(filepath.Join(dir, name))
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { d.Close() })
		run := func(q string, args ...any) {
			if _, err := d.Exec(q, args...); err != nil {
				t.Fatal(err)
			}
		}
		return &stream.Log{DB: d}, &Tasks{Reads: &reads.Env{DB: d}}, run
	}
	goLog, goTasks, goExec := open("go.sqlite")
	_, _, pyExec := open("py.sqlite")
	both := func(q string, args ...any) { goExec(q, args...); pyExec(q, args...) }
	both("INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'old', 'bob', 'human', 1)")

	cmd := osexec.Command("python3", "-I", "-c", pythonTasks, filepath.Join(dir, "py.sqlite"))
	cmd.Dir = root
	in, _ := cmd.StdinPipe()
	out, _ := cmd.StdoutPipe()
	if err := cmd.Start(); err != nil {
		t.Skip("no python3:", err)
	}
	defer cmd.Process.Kill()
	r := bufio.NewReader(out)
	if line, _ := r.ReadString('\n'); line != "ready\n" {
		cmd.Process.Kill()
		t.Skipf("the Python detector did not start: %q", line)
	}
	emit := func(e stream.Entry) (*stream.Event, error) { return goLog.Append(e) }
	if err := goTasks.Scan(goLog.DB, emit); err != nil {
		t.Fatal(err)
	}
	both("UPDATE tasks SET title = 'new', priority = 1 WHERE id = 1")
	both("INSERT INTO task_memos(task_id, author, body, created_at) VALUES(1, 'bob', 'note', 5)")
	both("INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by) VALUES(2, 'second', 'carol', 'human', 1, 'human')")
	if err := goTasks.Scan(goLog.DB, emit); err != nil {
		t.Fatal(err)
	}
	in.Write([]byte("\n"))
	line, _ := r.ReadString('\n')
	var py []map[string]any
	if err := json.Unmarshal([]byte(line), &py); err != nil {
		t.Fatalf("%q: %v", line, err)
	}
	events, _ := goLog.Since(0, []string{"tasks"}, "", 100)
	if len(py) != len(events) || len(py) != 2 {
		t.Fatalf("python wrote %d rows, go %d", len(py), len(events))
	}
	norm := func(v any) any {
		var out any
		switch s := v.(type) {
		case string:
			if json.Unmarshal([]byte(s), &out) != nil {
				return s
			}
			return out
		case json.RawMessage:
			json.Unmarshal(s, &out)
			return out
		}
		return v
	}
	// The Task Go's reads serve lacks six optional fields Python's carries (the spec marks them optional): Go's data must equal Python's on
	// every field it has, and the missing ones are named here, so closing the gap in the reads shows up as this list shrinking.
	missing := map[string]bool{"last_activity": true, "edited_by": true, "asker": true, "discarded": true, "mismatch": true, "requests": true}
	for i, e := range events {
		want := py[i]
		got := map[string]any{"topic": e.Topic, "type": e.Type, "agent": *e.Agent, "title": *e.Title, "target": norm(e.Target)}
		for k, v := range got {
			a, _ := json.Marshal(norm(want[k]))
			b, _ := json.Marshal(v)
			if string(a) != string(b) {
				t.Errorf("row %d %s: python %s, go %s", i, k, a, b)
			}
		}
		pyTask, _ := norm(want["data"]).(map[string]any)
		goTask, _ := norm(e.Data).(map[string]any)
		for k, v := range pyTask {
			g, has := goTask[k]
			switch {
			case !has && missing[k]:
			case !has:
				t.Errorf("row %d: Go's Task lacks %s", i, k)
			default:
				a, _ := json.Marshal(v)
				b, _ := json.Marshal(g)
				if string(a) != string(b) {
					t.Errorf("row %d %s: python %s, go %s", i, k, a, b)
				}
			}
		}
		for k := range goTask {
			if _, has := pyTask[k]; !has {
				t.Errorf("row %d: Go's Task has %s, Python's does not", i, k)
			}
		}
		for k := range missing {
			if _, has := goTask[k]; has {
				t.Errorf("row %d: Go now serves %s: take it off the missing list", i, k)
			}
		}
	}
}
