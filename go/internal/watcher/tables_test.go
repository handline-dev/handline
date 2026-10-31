package watcher

import (
	"context"
	"database/sql"
	"database/sql/driver"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"strings"
	"sync"
	"testing"

	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/stream"
)

// recorder keeps every statement the capture driver's connections were given.
var recorder struct {
	sync.Mutex
	on      bool
	queries []recorded
}

type recorded struct {
	sql  string
	args []any
}

func record(q string, args []driver.NamedValue) {
	recorder.Lock()
	defer recorder.Unlock()
	if !recorder.on {
		return
	}
	r := recorded{sql: q}
	for _, a := range args {
		if a.Name != "" {
			r.args = append(r.args, sql.Named(a.Name, a.Value))
		} else {
			r.args = append(r.args, a.Value)
		}
	}
	recorder.queries = append(recorder.queries, r)
}

type capDriver struct{ inner driver.Driver }

func (c capDriver) Open(name string) (driver.Conn, error) {
	cn, err := c.inner.Open(name)
	return &capConn{cn}, err
}

type capConn struct{ driver.Conn }

func (c *capConn) QueryContext(ctx context.Context, q string, args []driver.NamedValue) (driver.Rows, error) {
	qc, ok := c.Conn.(driver.QueryerContext)
	if !ok {
		return nil, driver.ErrSkip
	}
	record(q, args)
	return qc.QueryContext(ctx, q, args)
}

func (c *capConn) PrepareContext(ctx context.Context, q string) (driver.Stmt, error) {
	if pc, ok := c.Conn.(driver.ConnPrepareContext); ok {
		return pc.PrepareContext(ctx, q)
	}
	return c.Conn.Prepare(q)
}

func (c *capConn) BeginTx(ctx context.Context, o driver.TxOptions) (driver.Tx, error) {
	if bt, ok := c.Conn.(driver.ConnBeginTx); ok {
		return bt.BeginTx(ctx, o)
	}
	return c.Conn.Begin()
}

var registerCapture sync.Once

// captured opens the database at path through a driver that records the statements it is given.
func captured(t *testing.T, path string) *sql.DB {
	t.Helper()
	registerCapture.Do(func() {
		plain, _ := sql.Open("sqlite", "")
		sql.Register("sqlite-capture", capDriver{plain.Driver()})
	})
	d, err := sql.Open("sqlite-capture", "file:"+path+"?_pragma=busy_timeout(5000)")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	return d
}

// tablesOf is the tables (and the tables of the indexes) the statement opens, by its plan: EXPLAIN lists a root page for each b-tree it reads or writes.
func tablesOf(t *testing.T, d *sql.DB, r recorded) []string {
	t.Helper()
	rows, err := d.Query("EXPLAIN "+r.sql, r.args...)
	if err != nil {
		t.Fatalf("%v: %s", err, r.sql)
	}
	var roots []int
	for rows.Next() {
		var addr, p1, p2, p3 int
		var op string
		var p4, p5, comment sql.NullString
		if err := rows.Scan(&addr, &op, &p1, &p2, &p3, &p4, &p5, &comment); err != nil {
			t.Fatal(err)
		}
		switch op {
		case "OpenRead", "OpenWrite", "ReopenIdx":
			roots = append(roots, p2)
		}
	}
	rows.Close()
	seen := map[string]bool{}
	for _, root := range roots {
		var table string
		if err := d.QueryRow("SELECT tbl_name FROM sqlite_master WHERE rootpage = ?", root).Scan(&table); err == nil {
			seen[table] = true
		}
	}
	out := make([]string, 0, len(seen))
	for n := range seen {
		out = append(out, n)
	}
	sort.Strings(out)
	return out
}

// detectorsOver builds each detector over its own database, seeded, through the capture driver.
func detectorsOver(t *testing.T) []struct {
	det Detector
	db  *sql.DB
} {
	t.Helper()
	var out []struct {
		det Detector
		db  *sql.DB
	}
	for _, mk := range []func(*reads.Env) Detector{
		func(e *reads.Env) Detector { return &Tasks{Reads: e} }, func(*reads.Env) Detector { return &Agents{} },
		func(e *reads.Env) Detector { return &Notifications{Reads: e} }, func(e *reads.Env) Detector { return &Inbox{Reads: e} },
		func(e *reads.Env) Detector { return &ForYou{Reads: e} }, func(*reads.Env) Detector { return &Prefs{} },
		func(e *reads.Env) Detector { return &Requests{Reads: e} }, func(e *reads.Env) Detector { return &HR{Reads: e} },
		func(e *reads.Env) Detector { return &Chat{Reads: e} },
	} {
		path := filepath.Join(t.TempDir(), "db.sqlite")
		seed, err := store.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		seedWatched(t, seed)
		seed.Close()
		d := captured(t, path)
		det := mk(&reads.Env{DB: d, Home: t.TempDir()})
		out = append(out, struct {
			det Detector
			db  *sql.DB
		}{det, d})
	}
	return out
}

// touch is what a detector's statements were over a first look, a change in every table it could read, and a second look.
type touch struct {
	tables  []string
	queries []recorded
}

func touchedBy(t *testing.T, det Detector, d *sql.DB) touch {
	t.Helper()
	emit := func(stream.Entry) (*stream.Event, error) { return nil, nil }
	all := map[string]bool{}
	var out touch
	for i := 0; i < 3; i++ {
		if i == 1 {
			mutateWatched(t, d)
		}
		recorder.Lock()
		recorder.on, recorder.queries = true, nil
		recorder.Unlock()
		if err := det.Scan(d, emit); err != nil {
			t.Fatalf("%s: %v", det.Name(), err)
		}
		recorder.Lock()
		recorder.on = false
		qs := recorder.queries
		recorder.Unlock()
		out.queries = append(out.queries, qs...)
		for _, q := range qs {
			for _, tb := range tablesOf(t, d, q) {
				if tb != "table_changes" && tb != "sqlite_sequence" { // the triggers' own count
					all[tb] = true
				}
			}
		}
	}
	for n := range all {
		out.tables = append(out.tables, n)
	}
	sort.Strings(out.tables)
	return out
}

// A detector that reads a table it does not name would not run when that table changes, and a change would go unsaid.
func TestEachDetectorTouchesOnlyTheTablesItNames(t *testing.T) {
	for _, x := range detectorsOver(t) {
		named, ok := x.det.(Tabled)
		if !ok {
			t.Fatalf("%s is not Tabled", x.det.Name())
		}
		for _, tb := range touchedBy(t, x.det, x.db).tables {
			if !slices.Contains(named.Tables(), tb) {
				t.Errorf("%s reads %s and does not name it (names %v)", x.det.Name(), tb, named.Tables())
			}
		}
	}
}

// noisyEvents are the kinds of event the migration leaves uncounted: no detector reads them.
var noisyEvents = []string{"verb_chain", "rpc_write", "UserPromptSubmit", "Stop", "SubagentStop", "notified", "SessionStart", "SessionEnd"}

func TestNoDetectorReadsAnEventKindTheMigrationLeavesUncounted(t *testing.T) {
	migration, err := os.ReadFile("../store/migrations/0034_table_changes.sql")
	if err != nil {
		t.Fatal(err)
	}
	for _, k := range noisyEvents {
		if !strings.Contains(string(migration), "'"+k+"'") {
			t.Errorf("%s is not left uncounted by the migration", k)
		}
	}
	for _, x := range detectorsOver(t) {
		for _, q := range touchedBy(t, x.det, x.db).queries {
			text := q.sql + fmt.Sprint(q.args...)
			for _, k := range noisyEvents {
				if strings.Contains(text, "'"+k+"'") || strings.Contains(fmt.Sprint(q.args...), k) {
					t.Errorf("%s reads the event kind %s, which is not counted: %s", x.det.Name(), k, q.sql)
				}
			}
		}
	}
}

func TestEveryNamedTableIsCountedWithItsThreeTriggers(t *testing.T) {
	d, _ := rig(t)
	for _, x := range detectorsOver(t) {
		for _, tb := range x.det.(Tabled).Tables() {
			var n int
			if err := d.QueryRow("SELECT COUNT(*) FROM table_changes WHERE name = ?", tb).Scan(&n); err != nil || n != 1 {
				t.Errorf("%s names %s, which has no count: %v", x.det.Name(), tb, err)
			}
			for _, s := range []string{"i", "u", "d"} {
				if err := d.QueryRow("SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' AND name = ?", "tc_"+tb+"_"+s).Scan(&n); err != nil || n != 1 {
					t.Errorf("%s names %s, which has no tc_%s_%s trigger: %v", x.det.Name(), tb, tb, s, err)
				}
			}
		}
	}
}

func seedWatched(t *testing.T, d *sql.DB) {
	t.Helper()
	for _, q := range []string{
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'a', 'bob', 'human', 1), (2, 'b', 'human', 'bob', 1)",
		"INSERT INTO task_memos(task_id, author, body, created_at) VALUES(1, 'bob', 'note', 5)",
		"INSERT INTO requests(id, ts, requester, target, body, state, kind) VALUES(1, 100, 'bob', 'human', 'hi', 'queued', 'say'), (2, 100, 'human', 'bob', 'yo', 'landed', 'say')",
		"INSERT INTO events(ts, agent, kind, payload) VALUES(5, 'bob', 'task_new', '{\"task_id\":1}'), (6, 'bob', 'task_begin', '{\"task_id\":1}')",
	} {
		if _, err := d.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
}

func mutateWatched(t *testing.T, d *sql.DB) {
	t.Helper()
	for _, q := range []string{
		"UPDATE tasks SET title = 'a2', sent_at = 9, queued_by = 'human' WHERE id = 1",
		"INSERT INTO task_memos(task_id, author, body, created_at) VALUES(2, 'bob', 'n', 7)",
		"UPDATE requests SET state = 'landed', state_at = 300, landed_at = 300 WHERE id = 1",
		"INSERT INTO requests(id, ts, requester, target, body, state, kind, memo_id) VALUES(3, 300, 'bob', 'human', 'ask', 'queued', 'ask', NULL)",
		"INSERT INTO task_requests(task_id, request_id) VALUES(2, 3)",
		"INSERT INTO events(ts, agent, kind, payload) VALUES(8, 'bob', 'mark_task_done', '{\"task_id\":1}'), (9, 'bob', 'close_task', '{\"task_id\":1}'), (9, 'bob', 'login_expired', '{}')",
		"INSERT INTO agent_view(agent, view, updated) VALUES('bob', '{\"state\":\"stalled\"}', 1)",
		"INSERT INTO agent_state(agent, state, updated) VALUES('bob', 'idle', 1)",
		"INSERT INTO prompt_blocks(agent, kind, fingerprint, title, panel, detail, first_seen, last_seen) VALUES('bob', 'dialog', 'f', 't', '{}', 'd', 1, 1)",
		"INSERT INTO prefs(person, nav, updated_at) VALUES('human', '{}', 1)",
		"INSERT INTO cursors(name, row_id, updated) VALUES('for_you_seen:bob', 1, 1)",
		"INSERT INTO session_starts(agent, ts, session_id, cause, resumed, text, prompt) VALUES('bob', 1, 's', 'startup', 0, 't', 'p')",
	} {
		if _, err := d.Exec(q); err != nil {
			t.Fatalf("%v: %s", err, q)
		}
	}
}
