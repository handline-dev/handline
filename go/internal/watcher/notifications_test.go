package watcher

import (
	"database/sql"
	"testing"

	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/stream"
)

func notes(t *testing.T, l *stream.Log) []*stream.Event {
	t.Helper()
	got, err := l.Since(0, []string{"notifications"}, "", 100)
	if err != nil {
		t.Fatal(err)
	}
	return got
}

func addPrompt(t *testing.T, d *sql.DB, agent, title string) {
	t.Helper()
	exec(t, d, "INSERT INTO prompt_blocks(agent, kind, fingerprint, title, panel, detail, first_seen, last_seen) VALUES(?, 'permission', ?, ?, '[\"run ls?\"]', '{\"tool\":\"Bash\",\"command\":\"ls\"}', 5, 5)",
		agent, "fp-"+title, title)
}

func TestNotificationsAreSaidOnceFromTheCursorsAndHistoryIsNotNews(t *testing.T) {
	d, l := rig(t)
	exec(t, d, "INSERT INTO events(ts, agent, kind, payload) VALUES(1, 'bob', 'mark_task_done', '{\"task_id\": 1}')")
	addPrompt(t, d, "bob", "old")
	emit := func(e stream.Entry) (*stream.Event, error) { return l.Append(e) }
	n := &Notifications{Reads: &reads.Env{DB: d}}
	if err := n.Scan(d, emit); err != nil || len(notes(t, l)) != 0 {
		t.Fatalf("history was news: %v %v", err, notes(t, l))
	}
	exec(t, d, "INSERT INTO events(ts, agent, kind, payload) VALUES(2, 'bob', 'mark_task_done', '{\"task_id\": 7, \"done\": true}')")
	exec(t, d, "INSERT INTO events(ts, agent, kind, payload) VALUES(3, 'bob', 'mark_task_done', '{\"task_id\": 8, \"done\": false}')") // a reopening says nothing
	exec(t, d, "INSERT INTO events(ts, agent, kind, payload) VALUES(4, 'bob', 'mark_task_done', '{\"done\": true}')")                  // no task named
	exec(t, d, "INSERT INTO events(ts, agent, account, kind, payload) VALUES(5, 'amy', 'main', 'login_expired', 'not json')")
	exec(t, d, "INSERT INTO events(ts, agent, account, kind, payload) VALUES(6, 'amy', 'main', 'login_expired', '{\"account\": \"alt\"}')")
	exec(t, d, "INSERT INTO events(ts, agent, kind) VALUES(7, 'amy', 'something_else')")
	addPrompt(t, d, "amy", "Allow ls?")
	if err := n.Scan(d, emit); err != nil {
		t.Fatal(err)
	}
	got := notes(t, l)
	if len(got) != 4 {
		t.Fatalf("%d rows: %+v", len(got), got)
	}
	if got[0].Type != "task_done" || string(got[0].Data) != `{"task":7}` || *got[0].Title != "task 7 done" || string(got[0].Target) != `{"id":"7","screen":"task"}` || *got[0].Agent != "bob" {
		t.Fatalf("%+v %s", got[0], got[0].Data)
	}
	if got[1].Type != "account_out" || string(got[1].Data) != `{"account":"main"}` || *got[1].Title != "main needs logging in" || string(got[1].Target) != `{"id":"main","screen":"account"}` {
		t.Fatalf("%+v %s", got[1], got[1].Data)
	}
	if string(got[2].Data) != `{"account":"alt"}` {
		t.Fatalf("%s", got[2].Data)
	}
	if got[3].Type != "prompt_asked" || *got[3].Agent != "amy" || *got[3].Title != "amy asks: Allow ls?" || string(got[3].Target) != `{"id":"amy","screen":"agent"}` {
		t.Fatalf("%+v", got[3])
	}
	if err := n.Scan(d, emit); err != nil || len(notes(t, l)) != 4 {
		t.Fatalf("a second look repeated: %v", len(notes(t, l)))
	}
}

func TestARestartResumesFromTheCursorsNothingRepeatedNothingLostWhileDown(t *testing.T) {
	d, l := rig(t)
	emit := func(e stream.Entry) (*stream.Event, error) { return l.Append(e) }
	first := &Notifications{Reads: &reads.Env{DB: d}}
	first.Scan(d, emit) // primes the cursors on an empty database
	exec(t, d, "INSERT INTO events(ts, agent, kind, payload) VALUES(1, 'bob', 'mark_task_done', '{\"task_id\": 1}')")
	first.Scan(d, emit)
	if len(notes(t, l)) != 1 {
		t.Fatalf("%d", len(notes(t, l)))
	}
	// the daemon is down: a task is marked done and a prompt opens
	exec(t, d, "INSERT INTO events(ts, agent, kind, payload) VALUES(2, 'bob', 'mark_task_done', '{\"task_id\": 2}')")
	addPrompt(t, d, "bob", "while down")
	second := &Notifications{Reads: &reads.Env{DB: d}}
	if err := second.Scan(d, emit); err != nil {
		t.Fatal(err)
	}
	got := notes(t, l)
	if len(got) != 3 || string(got[1].Data) != `{"task":2}` || got[2].Type != "prompt_asked" {
		t.Fatalf("%d rows: %+v", len(got), got)
	}
	if err := second.Scan(d, emit); err != nil || len(notes(t, l)) != 3 {
		t.Fatal("repeated after the restart")
	}
}

func TestAFailedEmitDoesNotRepeatTheRowsBeforeIt(t *testing.T) {
	d, l := rig(t)
	n := &Notifications{Reads: &reads.Env{DB: d}}
	n.Scan(d, func(e stream.Entry) (*stream.Event, error) { return l.Append(e) })
	for i := 1; i <= 3; i++ {
		exec(t, d, "INSERT INTO events(ts, agent, kind, payload) VALUES(?, 'bob', 'mark_task_done', ?)", i, `{"task_id": `+string(rune('0'+i))+`}`)
	}
	calls := 0
	failing := func(e stream.Entry) (*stream.Event, error) {
		calls++
		if calls == 2 {
			return nil, sql.ErrConnDone
		}
		return l.Append(e)
	}
	if err := n.Scan(d, failing); err == nil {
		t.Fatal("the failure was swallowed")
	}
	if err := n.Scan(d, func(e stream.Entry) (*stream.Event, error) { return l.Append(e) }); err != nil {
		t.Fatal(err)
	}
	got := notes(t, l)
	if len(got) != 3 || string(got[0].Data) != `{"task":1}` || string(got[1].Data) != `{"task":2}` || string(got[2].Data) != `{"task":3}` {
		t.Fatalf("%d rows", len(got))
	}
}

func TestGoAndPythonWriteTheSameNotifications(t *testing.T) {
	py, events := parity(t, "_notifications", func(d *sql.DB) Detector { return &Notifications{Reads: &reads.Env{DB: d}} },
		func(run func(string, ...any)) {
			run("INSERT INTO events(ts, agent, kind, payload) VALUES(1, 'bob', 'mark_task_done', '{\"task_id\": 1}')")
			run("INSERT INTO prompt_blocks(agent, kind, fingerprint, title, panel, detail, first_seen, last_seen) VALUES('bob', 'permission', 'old', 'old', '[]', '{}', 1, 1)")
		},
		func(run func(string, ...any)) {
			run("INSERT INTO events(ts, agent, kind, payload) VALUES(2, 'bob', 'mark_task_done', '{\"task_id\": 7, \"done\": true}')")
			run("INSERT INTO events(ts, agent, kind, payload) VALUES(3, 'bob', 'mark_task_done', '{\"task_id\": 8, \"done\": false}')")
			run("INSERT INTO events(ts, agent, kind, payload) VALUES(4, 'bob', 'mark_task_done', '{\"done\": true}')")
			run("INSERT INTO events(ts, agent, account, kind, payload) VALUES(5, 'amy', 'main', 'login_expired', 'not json')")
			run("INSERT INTO events(ts, agent, account, kind, payload) VALUES(6, 'amy', 'main', 'login_expired', '{\"account\": \"alt\"}')")
			run("INSERT INTO prompt_blocks(agent, kind, fingerprint, title, panel, detail, first_seen, last_seen) VALUES('amy', 'permission', 'fp1', 'Allow ls?', '[\"run ls?\"]', '{\"tool\":\"Bash\",\"command\":\"ls\",\"reason\":\"r\"}', 5, 9)")
			run("INSERT INTO prompt_blocks(agent, kind, fingerprint, title, panel, detail, first_seen, last_seen) VALUES('cat', 'question', 'fp2', 'Pick one', '[\"a\",\"b\"]', '{\"options\":[{\"label\":\"x\"},{\"label\":\"y\",\"checked\":true}],\"multi_select\":true}', 6, 9)")
		})
	if len(py) != len(events) || len(py) != 5 {
		t.Fatalf("python wrote %d rows, go %d", len(py), len(events))
	}
	for i, e := range events {
		sameRow(t, i, py[i], e)
		if !same(py[i]["data"], e.Data) {
			t.Errorf("row %d data: python %v, go %s", i, py[i]["data"], e.Data)
		}
	}
}
