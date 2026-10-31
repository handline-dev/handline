package watcher

import (
	"context"
	"database/sql"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/cmdruns"
	"github.com/handline/handline/go/internal/stream"
	"github.com/handline/handline/go/internal/term"
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

// failsOnce is a Tabled detector whose first run fails.
type failsOnce struct {
	tables []string
	runs   int
}

func (f *failsOnce) Name() string { return "fails-once" }
func (f *failsOnce) Scan(*sql.DB, Emit) error {
	f.runs++
	if f.runs == 1 {
		return sql.ErrConnDone
	}
	return nil
}
func (f *failsOnce) Tables() []string { return f.tables }

// A scan that failed is run again at the next look, as on every commit before the counts.
func TestAScanThatFailedIsRunAgainAtTheNextLook(t *testing.T) {
	_, _, w, look := gate(t)
	byTable, byFile := &failsOnce{tables: []string{"task_memos"}}, &failsOnce{}
	w.Detectors = []Detector{byTable, byFile}
	look(true)
	look(false)
	look(false)
	if byTable.runs != 2 || byFile.runs != 2 {
		t.Fatalf("ran %d and %d, want 2 each (the failure, the retry, and no more once it succeeded)", byTable.runs, byFile.runs)
	}
}

// Each counted table moves its own count on an insert, an update and a delete, whoever writes.
func TestEveryCountedTableMovesItsOwnCountOnInsertUpdateAndDelete(t *testing.T) {
	d, _ := rig(t)
	exec(t, d, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'a', 'bob', 'human', 1), (2, 'b', 'bob', 'human', 1)")
	exec(t, d, "INSERT INTO requests(id, ts, requester, target, body) VALUES(1, 1, 'bob', 'human', 'x')")
	count := func(table string) (n int64) {
		if err := d.QueryRow("SELECT n FROM table_changes WHERE name = ?", table).Scan(&n); err != nil {
			t.Fatal(err)
		}
		return n
	}
	for _, c := range []struct {
		table                string
		insert, update, drop string
	}{
		{"agent_state", "INSERT INTO agent_state(agent, state, updated) VALUES('a', 'idle', 1)", "UPDATE agent_state SET state = 'x'", "DELETE FROM agent_state"},
		{"agent_view", "INSERT INTO agent_view(agent, view, updated) VALUES('a', '{}', 1)", "UPDATE agent_view SET view = '[]'", "DELETE FROM agent_view"},
		{"cursors", "INSERT INTO cursors(name, row_id, updated) VALUES('for_you_seen:a', 1, 1)", "UPDATE cursors SET row_id = 2 WHERE name = 'for_you_seen:a'", "DELETE FROM cursors WHERE name = 'for_you_seen:a'"},
		{"events", "INSERT INTO events(ts, agent, kind) VALUES(1, 'a', 'task_new')", "UPDATE events SET ts = 2", "DELETE FROM events"},
		{"meta", "INSERT INTO meta(name, value, updated) VALUES('m', 'v', 1)", "UPDATE meta SET value = 'w'", "DELETE FROM meta"},
		{"operations", "INSERT INTO operations(id, kind, state, created_at, updated_at) VALUES('o', 'k', 's', 1, 1)", "UPDATE operations SET state = 't'", "DELETE FROM operations"},
		{"prefs", "INSERT INTO prefs(person, nav, updated_at) VALUES('p', '{}', 1)", "UPDATE prefs SET nav = '[]'", "DELETE FROM prefs"},
		{"prompt_blocks", "INSERT INTO prompt_blocks(agent, kind, fingerprint, title, panel, detail, first_seen, last_seen) VALUES('a', 'dialog', 'f', 't', '{}', 'd', 1, 1)",
			"UPDATE prompt_blocks SET title = 'u'", "DELETE FROM prompt_blocks"},
		{"requests", "INSERT INTO requests(id, ts, requester, target, body) VALUES(2, 1, 'bob', 'human', 'y')", "UPDATE requests SET body = 'z' WHERE id = 2", "DELETE FROM requests WHERE id = 2"},
		{"session_starts", "INSERT INTO session_starts(agent, ts, session_id, cause, resumed, text, prompt) VALUES('a', 1, 's', 'c', 0, 't', 'p')",
			"UPDATE session_starts SET ts = 2", "DELETE FROM session_starts"},
		{"task_blocks", "INSERT INTO task_blocks(task_id, blocks_on, created_at) VALUES(1, 2, 1)", "UPDATE task_blocks SET dormant = 1", "DELETE FROM task_blocks"},
		{"task_memos", "INSERT INTO task_memos(task_id, author, body, created_at) VALUES(1, 'a', 'n', 1)", "UPDATE task_memos SET body = 'o'", "DELETE FROM task_memos"},
		{"task_requests", "INSERT INTO task_requests(task_id, request_id) VALUES(1, 1)", "UPDATE task_requests SET request_id = 2", "DELETE FROM task_requests"},
		{"task_tags", "INSERT INTO task_tags(task_id, tag) VALUES(1, 'a')", "UPDATE task_tags SET tag = 'b'", "DELETE FROM task_tags"},
		{"tasks", "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(3, 'c', 'bob', 'human', 1)", "UPDATE tasks SET title = 'd' WHERE id = 3", "DELETE FROM tasks WHERE id = 3"},
	} {
		for _, q := range []string{c.insert, c.update, c.drop} {
			before := count(c.table)
			exec(t, d, q)
			if after := count(c.table); after <= before { // a trigger of the table's own (requests_ref) may change it again
				t.Errorf("%s: the count went %d to %d on %s", c.table, before, after, q)
			}
		}
	}
	// The two counted in part leave out what no detector reads.
	for _, q := range []string{
		"INSERT INTO events(ts, agent, kind) VALUES(1, 'a', 'verb_chain')", "UPDATE events SET ts = 2 WHERE kind = 'verb_chain'", "DELETE FROM events WHERE kind = 'verb_chain'",
		"INSERT INTO cursors(name, row_id, updated) VALUES('stream:a', 1, 1)", "UPDATE cursors SET row_id = 2 WHERE name = 'stream:a'", "DELETE FROM cursors WHERE name = 'stream:a'",
	} {
		events, cursors := count("events"), count("cursors")
		exec(t, d, q)
		if count("events") != events || count("cursors") != cursors {
			t.Errorf("a change nothing reads moved a count: %s", q)
		}
	}
}

// The detector says it is pending while a command run is open and the terminal host is asked, and not otherwise.
func TestChatIsPendingWhileACommandRunIsOpen(t *testing.T) {
	r := newChatRig(t)
	exec(t, r.d, "INSERT INTO requests(id, ts, requester, target, body, state, kind) VALUES(1, 100, 'bob', 'human', 'run it', 'landed', 'say')")
	started := cmdruns.Now() - 10
	panes := map[string]term.PaneRun{}
	c := r.chat()
	c.Panes = func() map[string]term.PaneRun { return panes }
	r.scan(c)
	if c.Pending() {
		t.Fatal("pending with no run")
	}
	run, err := cmdruns.Start(r.d, cmdruns.Block{Message: 1, N: 0}, "%4", started)
	if err != nil {
		t.Fatal(err)
	}
	panes["%4"] = term.PaneRun{Tag: run.Tag()}
	r.scan(c)
	if !c.Pending() {
		t.Fatal("not pending with a run open")
	}
	c.Panes = nil
	if c.Pending() {
		t.Fatal("pending without a terminal host to ask")
	}
	c.Panes = func() map[string]term.PaneRun { return panes }
	panes["%4"] = term.PaneRun{Tag: run.Tag(), Dead: true, Status: 0, DeadTime: started + 3}
	r.scan(c)
	if c.Pending() {
		t.Fatal("pending after the run ended")
	}
}

// pends signals each run on a channel and says it is pending.
type pends struct{ ran chan struct{} }

func (p pends) Name() string             { return "pends" }
func (p pends) Scan(*sql.DB, Emit) error { p.ran <- struct{}{}; return nil }
func (p pends) Tables() []string         { return nil }
func (p pends) Pending() bool            { return true }

// A pending detector is looked at again on a tick that has no commit: the loop wakes for it.
func TestARunLoopLooksAgainOnATickWithoutACommitWhileADetectorIsPending(t *testing.T) {
	d, _ := rig(t)
	p := pends{ran: make(chan struct{}, 8)}
	w := &Watcher{DB: d, Emit: func(stream.Entry) (*stream.Event, error) { return nil, nil }, Log: func(string, ...any) {}, Detectors: []Detector{p}}
	stop := make(chan struct{})
	done := make(chan struct{})
	go func() { w.Run(stop); close(done) }()
	for i := 0; i < 3; i++ {
		select {
		case <-p.ran:
		case <-time.After(10 * time.Second):
			t.Fatalf("run %d of the pending detector never came", i+1)
		}
	}
	close(stop)
	<-done
}
