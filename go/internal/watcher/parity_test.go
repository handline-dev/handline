package watcher

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/stream"
)

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

// parity runs the Go detector over a database that setup and then change are applied to, and returns the rows the Python watcher wrote
// for the same changes (recorded from handline/daemon/stream.py before it was deleted: testdata/parity/<test name>.json) and the rows Go
// wrote.
func parity(t *testing.T, detector func(*sql.DB) Detector, setup, change func(run func(string, ...any))) (py []map[string]any, goRows []*stream.Event) {
	t.Helper()
	recorded, err := os.ReadFile(filepath.Join("testdata", "parity", t.Name()+".json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(recorded, &py); err != nil {
		t.Fatal(err)
	}
	d, err := store.Open(filepath.Join(t.TempDir(), "go.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	run := func(q string, args ...any) {
		if _, err := d.Exec(q, args...); err != nil {
			t.Fatal(err)
		}
	}
	log := &stream.Log{DB: d}
	setup(run)
	det := detector(d)
	emit := func(e stream.Entry) (*stream.Event, error) { return log.Append(e) }
	if err := det.Scan(d, emit); err != nil {
		t.Fatal(err)
	}
	change(run)
	if err := det.Scan(d, emit); err != nil {
		t.Fatal(err)
	}
	goRows, err = log.Since(0, []string{"agents", "tasks", "notifications", "inbox", "hr", "chat"}, "", 100)
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
	py, events := parity(t, func(d *sql.DB) Detector { return &Tasks{Reads: &reads.Env{DB: d}} },
		func(run func(string, ...any)) {
			run("INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'old', 'bob', 'human', 1)")
		},
		func(run func(string, ...any)) {
			run("UPDATE tasks SET title = 'new', priority = 1 WHERE id = 1")
			run("INSERT INTO task_memos(task_id, author, body, created_at) VALUES(1, 'bob', 'note', 5)")
			run("INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by) VALUES(2, 'second', 'carol', 'human', 1, 'human')")
			// one an open blocker holds, and one doing past its deadline (the clock counts from the deadline missed)
			run("INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by) VALUES(3, 'held', 'carol', 'human', 1, 'human')")
			run("INSERT INTO task_blocks(task_id, blocks_on, created_at) VALUES(3, 1, 1)")
			run("INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, sent_at, timeout_s, timeout_at) VALUES(4, 'late', 'dave', 'human', 1, 'human', 1, 3600, 100)")
			// a draft with a request out (its mismatch names the request), and the requests linked to a task, oldest first
			run("INSERT INTO tasks(id, title, owner, opened_by, created_at, sent_at) VALUES(5, 'unapproved', 'erin', 'human', 1, 1)")
			run("INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok) VALUES(51, 7, 'human', 'erin', 'b', 'queued', 'task', 1)")
			run("INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok) VALUES(50, 6, 'mgr', 'erin', 'b', 'landed', 'say', 1)")
			run("INSERT INTO task_requests(task_id, request_id) VALUES(5, 51)")
			run("INSERT INTO task_requests(task_id, request_id) VALUES(5, 50)")
		})
	if len(py) != len(events) || len(py) != 5 {
		t.Fatalf("python wrote %d rows, go %d", len(py), len(events))
	}
	// Go's data must equal Python's on every field of the Task: the Python CLI reads them all.
	for i, e := range events {
		sameRow(t, i, py[i], e)
		pyTask, _ := norm(py[i]["data"]).(map[string]any)
		goTask, _ := norm(e.Data).(map[string]any)
		for k, v := range pyTask {
			g, has := goTask[k]
			switch {
			case !has:
				t.Errorf("row %d: Go's Task lacks %s", i, k)
			case !same(v, g):
				t.Errorf("row %d %s: python %v, go %v", i, k, v, g)
			}
		}
		switch fmt.Sprint(goTask["id"]) {
		case "3":
			if fmt.Sprint(goTask["blocked_by"]) != "[1]" {
				t.Errorf("the held task lists its blocker: %v", goTask["blocked_by"])
			}
		case "2":
			if goTask["mismatch"] != "approved, but no request is out" {
				t.Errorf("a queued task nothing is out for says so: %v", goTask["mismatch"])
			}
		case "5":
			if goTask["mismatch"] != "draft with work out (#51) - its opener has not queued it" {
				t.Errorf("a draft with work out names the request: %v", goTask["mismatch"])
			}
			if reqs, _ := goTask["requests"].([]any); len(reqs) != 2 {
				t.Errorf("both linked requests, oldest first: %v", goTask["requests"])
			}
		case "4":
			if c, _ := goTask["time"].(map[string]any); c == nil || c["overdue_since"] == nil {
				t.Errorf("the late task says since when: %v", goTask["time"])
			}
		}
		for k := range goTask {
			if _, has := pyTask[k]; !has {
				t.Errorf("row %d: Go's Task has %s, Python's does not", i, k)
			}
		}
	}
}

func TestGoAndPythonWriteTheSameRowsForTheSameAgentChanges(t *testing.T) {
	v := func(name, state string) string { return `{"name":"` + name + `","state":"` + state + `","role":"r"}` }
	py, events := parity(t, func(*sql.DB) Detector { return &Agents{} },
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
	py, events := parity(t, func(d *sql.DB) Detector { return &Chat{Reads: &reads.Env{DB: d}} },
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
