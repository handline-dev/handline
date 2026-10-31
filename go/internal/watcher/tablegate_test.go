package watcher

import (
	"context"
	"database/sql"
	"testing"

	"github.com/handline/handline/go/internal/stream"
)

// counted is a detector that counts its runs.
type counted struct {
	tables  []string
	runs    int
	pending bool
	untied  bool // not Tabled
}

func (c *counted) Name() string { return "counted" }
func (c *counted) Scan(*sql.DB, Emit) error {
	c.runs++
	return nil
}

type tabledCounted struct{ *counted }

func (c tabledCounted) Tables() []string { return c.tables }

type pendingCounted struct{ tabledCounted }

func (c pendingCounted) Pending() bool { return c.pending }

func gate(t *testing.T) (*sql.DB, *sql.Conn, *Watcher, func(all bool)) {
	t.Helper()
	d, _ := rig(t)
	conn, err := d.Conn(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	w := &Watcher{DB: d, Emit: func(stream.Entry) (*stream.Event, error) { return nil, nil }, Log: func(string, ...any) {}}
	return d, conn, w, func(all bool) { w.scanMoved(context.Background(), conn, all) }
}

func TestADetectorRunsOnlyWhenATableItNamesChanged(t *testing.T) {
	d, _, w, look := gate(t)
	tasks := &counted{tables: []string{"tasks", "task_memos"}}
	reqs := &counted{tables: []string{"requests"}}
	w.Detectors = []Detector{tabledCounted{tasks}, tabledCounted{reqs}}
	exec(t, d, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 't', 'bob', 'human', 1)")
	look(true)
	look(false)
	if tasks.runs != 1 || reqs.runs != 1 {
		t.Fatalf("the first look ran %d and %d, and nothing had changed", tasks.runs, reqs.runs)
	}
	// What no detector reads: an event of a noisy kind, a cursor of the watcher's own, a row of a table that is not counted at all.
	exec(t, d, "INSERT INTO events(ts, agent, kind) VALUES(1, 'bob', 'verb_chain')")
	exec(t, d, "INSERT INTO cursors(name, row_id, updated) VALUES('stream:x', 1, 1)")
	exec(t, d, "INSERT INTO stream_log(ts, topic, type, data) VALUES(1, 't', 'x', '{}')")
	exec(t, d, "INSERT INTO rpc_replies(caller, request_id, method, params, reply, ts) VALUES('c', 'r', 'm', '{}', '{}', 1)")
	look(false)
	if tasks.runs != 1 || reqs.runs != 1 {
		t.Fatalf("a commit nothing reads ran %d and %d", tasks.runs, reqs.runs)
	}
	exec(t, d, "INSERT INTO task_memos(task_id, author, body, created_at) VALUES(1, 'bob', 'n', 1)")
	look(false)
	if tasks.runs != 2 || reqs.runs != 1 {
		t.Fatalf("a memo ran %d and %d, want 2 and 1", tasks.runs, reqs.runs)
	}
	exec(t, d, "UPDATE task_memos SET body = 'x'")
	exec(t, d, "DELETE FROM task_memos")
	look(false)
	if tasks.runs != 3 {
		t.Fatalf("an update and a delete in one look ran %d, want 3", tasks.runs)
	}
	exec(t, d, "INSERT INTO events(ts, agent, kind) VALUES(1, 'bob', 'task_new')")
	exec(t, d, "INSERT INTO cursors(name, row_id, updated) VALUES('for_you_seen:bob', 1, 1)")
	look(false)
	if tasks.runs != 3 || reqs.runs != 1 {
		t.Fatalf("an event and a cursor no one here names ran %d and %d", tasks.runs, reqs.runs)
	}
}

// A detector runs at every look when it must: the first look of a moved Signature (all), or a table it names that has no count.
func TestADetectorRunsAtOnceWhenTheSignatureMovedOrATableHasNoCount(t *testing.T) {
	for _, c := range []struct {
		table string
		all   bool
	}{{"tasks", true}, {"no_such_table", false}} {
		_, _, w, look := gate(t)
		one := &counted{tables: []string{c.table}}
		w.Detectors = []Detector{tabledCounted{one}}
		look(true)
		look(c.all)
		if one.runs != 2 {
			t.Fatalf("%s: ran %d, want 2", c.table, one.runs)
		}
	}
}

func TestADetectorWithoutTablesRunsOnEveryChangeAndAPendingOneOnEveryLook(t *testing.T) {
	_, _, w, look := gate(t)
	loose := &counted{}
	waits := &counted{tables: []string{"tasks"}, pending: true}
	w.Detectors = []Detector{loose, pendingCounted{tabledCounted{waits}}}
	look(true)
	look(false)
	look(false)
	if loose.runs != 3 || waits.runs != 3 {
		t.Fatalf("ran %d and %d, want 3 and 3", loose.runs, waits.runs)
	}
	waits.pending = false
	look(false)
	if waits.runs != 3 {
		t.Fatalf("a detector no longer pending ran again: %d", waits.runs)
	}
}

func TestWithoutTheCountsEveryDetectorRuns(t *testing.T) {
	d, _, w, look := gate(t)
	one := &counted{tables: []string{"tasks"}}
	w.Detectors = []Detector{tabledCounted{one}}
	look(true)
	exec(t, d, "DROP TABLE table_changes")
	look(false)
	look(false)
	if one.runs != 3 {
		t.Fatalf("ran %d, want 3", one.runs)
	}
}
