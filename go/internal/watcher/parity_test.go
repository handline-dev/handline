package watcher

import (
	"bufio"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	osexec "os/exec"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/stream"
)

// The Python watcher's detector, run on a copy of the database the Go one watches: the same first look, the same changes, and the rows it
// writes are the ones Go writes (the topic, type, agent, title, target and data). %s is the detector's name in handline/daemon/stream.py.
const pythonDetector = `
import json, os, sqlite3, sys
sys.path.insert(0, os.getcwd())
from handline.daemon import stream
con = sqlite3.connect(sys.argv[1]); con.row_factory = sqlite3.Row
state = {}
stream.%s(con, state); con.commit()
print("ready", flush=True)
sys.stdin.readline()
stream.%s(con, state); con.commit()
print(json.dumps([dict(r) for r in con.execute("SELECT topic, type, agent, title, target, data FROM stream_log ORDER BY id")]))
`

func norm(v any) any {
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

func same(a, b any) bool {
	x, _ := json.Marshal(norm(a))
	y, _ := json.Marshal(norm(b))
	return string(x) == string(y)
}

// parity runs pyName (a detector of the Python watcher) and the Go detector over two databases that setup and then change are applied to
// alike, and returns the rows Python wrote, and the rows Go wrote.
func parity(t *testing.T, pyName string, detector func(*sql.DB) Detector, setup, change func(run func(string, ...any))) (py []map[string]any, goRows []*stream.Event) {
	t.Helper()
	root, _ := filepath.Abs("../../..")
	dir := t.TempDir()
	type side struct {
		log *stream.Log
		run func(string, ...any)
	}
	open := func(name string) side {
		d, err := store.Open(filepath.Join(dir, name))
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { d.Close() })
		return side{&stream.Log{DB: d}, func(q string, args ...any) {
			if _, err := d.Exec(q, args...); err != nil {
				t.Fatal(err)
			}
		}}
	}
	g, p := open("go.sqlite"), open("py.sqlite")
	both := func(q string, args ...any) { g.run(q, args...); p.run(q, args...) }
	setup(both)

	script := fmt.Sprintf(pythonDetector, pyName, pyName)
	cmd := osexec.Command("python3", "-I", "-c", script, filepath.Join(dir, "py.sqlite"))
	cmd.Dir = root
	cmd.Stderr = os.Stderr
	in, _ := cmd.StdinPipe()
	out, _ := cmd.StdoutPipe()
	if err := cmd.Start(); err != nil {
		t.Skip("no python3:", err)
	}
	defer cmd.Process.Kill()
	r := bufio.NewReader(out)
	if line, _ := r.ReadString('\n'); line != "ready\n" {
		t.Skipf("the Python detector did not start: %q", line)
	}
	d := detector(g.log.DB)
	emit := func(e stream.Entry) (*stream.Event, error) { return g.log.Append(e) }
	if err := d.Scan(g.log.DB, emit); err != nil {
		t.Fatal(err)
	}
	change(both)
	if err := d.Scan(g.log.DB, emit); err != nil {
		t.Fatal(err)
	}
	in.Write([]byte("\n"))
	line, _ := r.ReadString('\n')
	if err := json.Unmarshal([]byte(line), &py); err != nil {
		t.Fatalf("%q: %v", line, err)
	}
	goRows, err := g.log.Since(0, []string{"agents", "tasks", "notifications", "inbox", "chat"}, "", 100)
	if err != nil {
		t.Fatal(err)
	}
	return py, goRows
}

// sameRow reports the differences between a Python row and a Go one on topic, type, agent, title, target; data is the caller's.
func sameRow(t *testing.T, i int, want map[string]any, e *stream.Event) {
	t.Helper()
	agent, title := any(nil), any(nil)
	if e.Agent != nil {
		agent = *e.Agent
	}
	if e.Title != nil {
		title = *e.Title
	}
	for k, v := range map[string]any{"topic": e.Topic, "type": e.Type, "agent": agent, "title": title, "target": e.Target} {
		if !same(want[k], v) {
			a, _ := json.Marshal(norm(want[k]))
			b, _ := json.Marshal(norm(v))
			t.Errorf("row %d %s: python %s, go %s", i, k, a, b)
		}
	}
}

func TestGoAndPythonWriteTheSameRowsForTheSameTaskChange(t *testing.T) {
	py, events := parity(t, "_tasks", func(d *sql.DB) Detector { return &Tasks{Reads: &reads.Env{DB: d}} },
		func(run func(string, ...any)) {
			run("INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'old', 'bob', 'human', 1)")
		},
		func(run func(string, ...any)) {
			run("UPDATE tasks SET title = 'new', priority = 1 WHERE id = 1")
			run("INSERT INTO task_memos(task_id, author, body, created_at) VALUES(1, 'bob', 'note', 5)")
			run("INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by) VALUES(2, 'second', 'carol', 'human', 1, 'human')")
		})
	if len(py) != len(events) || len(py) != 2 {
		t.Fatalf("python wrote %d rows, go %d", len(py), len(events))
	}
	// The Task Go's reads serve leaves out six optional fields Python's carries, on purpose: no app reads them from a Task (the asker column
	// sorts on `from`, a discarded task is told by its state). Go's data must equal Python's on every field it has.
	missing := map[string]bool{"last_activity": true, "edited_by": true, "asker": true, "discarded": true, "mismatch": true, "requests": true}
	for i, e := range events {
		sameRow(t, i, py[i], e)
		pyTask, _ := norm(py[i]["data"]).(map[string]any)
		goTask, _ := norm(e.Data).(map[string]any)
		for k, v := range pyTask {
			g, has := goTask[k]
			switch {
			case !has && missing[k]:
			case !has:
				t.Errorf("row %d: Go's Task lacks %s", i, k)
			case !same(v, g):
				t.Errorf("row %d %s: python %v, go %v", i, k, v, g)
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

func TestGoAndPythonWriteTheSameRowsForTheSameAgentChanges(t *testing.T) {
	v := func(name, state string) string { return `{"name":"` + name + `","state":"` + state + `","role":"r"}` }
	py, events := parity(t, "_agents", func(*sql.DB) Detector { return &Agents{} },
		func(run func(string, ...any)) {
			run("INSERT INTO agent_view(agent, view, updated) VALUES('amy', ?, 0), ('bob', ?, 0), ('cat', ?, 0)", v("amy", "idle"), v("bob", "idle"), v("cat", "idle"))
		},
		func(run func(string, ...any)) {
			run("UPDATE agent_view SET view = ? WHERE agent = 'amy'", v("amy", "stalled"))
			run("UPDATE agent_view SET view = ? WHERE agent = 'bob'", v("bob", "running"))
			run("DELETE FROM agent_view WHERE agent = 'cat'")
			run("INSERT INTO agent_view(agent, view, updated) VALUES('dan', ?, 0)", v("dan", "stalled"))
		})
	if len(py) != len(events) || len(py) != 6 {
		t.Fatalf("python wrote %d rows, go %d", len(py), len(events))
	}
	for i, e := range events {
		sameRow(t, i, py[i], e)
		if !same(py[i]["data"], e.Data) {
			t.Errorf("row %d data: python %v, go %s", i, py[i]["data"], e.Data)
		}
	}
}

func TestGoAndPythonWriteTheSameRowsForTheSameChatChanges(t *testing.T) {
	ins := "INSERT INTO requests(id, ts, requester, target, body, state, kind, settles) VALUES(?, ?, ?, ?, ?, ?, 'say', ?)"
	pb := "INSERT INTO prompt_blocks(agent, kind, fingerprint, title, panel, detail, answerable, first_seen, last_seen) VALUES(?, 'question', 'f', 't', '[]', '{}', 1, ?, ?)"
	py, events := parity(t, "_chat", func(d *sql.DB) Detector { return &Chat{Reads: &reads.Env{DB: d}} },
		func(run func(string, ...any)) {
			run("INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 't', 'bob', 'human', 1)")
			run("INSERT INTO agent_view(agent, view, updated) VALUES('bob', ?, 0), ('amy', ?, 0)", `{"name":"bob","state":"idle"}`, `{"name":"amy","state":"idle"}`)
			run(pb, "amy", 1, 1)
		},
		func(run func(string, ...any)) {
			run(ins, 1, 100, "human", "bob", "question", "landed", nil)
			run(ins, 2, 150, "bob", "human", "the answer", "landed", 1)
			run(ins, 3, 200, "amy", "bob", "hello", "landed", nil)
			run("INSERT INTO events(ts, agent, kind, payload) VALUES(5, 'bob', 'task_begin', '{\"task_id\":1}')")
			run(pb, "bob", 6, 6)
			run("UPDATE prompt_blocks SET gone_at = 9 WHERE id = 1")
			run("UPDATE agent_view SET view = ? WHERE agent = 'bob'", `{"name":"bob","state":"running","state_since":"x","blocker":"b"}`)
		})
	if len(py) != len(events) || len(py) < 8 {
		t.Fatalf("python wrote %d rows, go %d", len(py), len(events))
	}
	for i, e := range events {
		sameRow(t, i, py[i], e)
		if !same(py[i]["data"], e.Data) {
			t.Errorf("row %d data: python %v, go %s", i, py[i]["data"], e.Data)
		}
	}
}
