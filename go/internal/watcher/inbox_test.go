package watcher

import (
	"database/sql"
	"encoding/json"
	"path/filepath"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/stream"
)

const t0 = 1_000_000

func inboxRows(t *testing.T, l *stream.Log) []*stream.Event {
	t.Helper()
	got, err := l.Since(0, []string{"inbox"}, "", 100)
	if err != nil {
		t.Fatal(err)
	}
	return got
}

func request(t *testing.T, d *sql.DB, id, ts int64, requester, target, body string) {
	t.Helper()
	exec(t, d, "INSERT INTO requests(id, ts, requester, target, body, state, edge_ok) VALUES(?, ?, ?, ?, ?, 'queued', 1)", id, ts, requester, target, body)
}

func TestTheInboxIsToldWhenAnItemArrivesChangesOrLeavesAndTheFirstLookSaysNothing(t *testing.T) {
	d, l := rig(t)
	request(t, d, 1, t0-100, "amy", "bob", "old for bob")
	request(t, d, 2, t0-100, "amy", "human", "old for you")
	in := &Inbox{Reads: &reads.Env{DB: d}, Now: func() time.Time { return time.Unix(t0, 0) }}
	emit := func(e stream.Entry) (*stream.Event, error) { return l.Append(e) }
	if err := in.Scan(d, emit); err != nil || len(inboxRows(t, l)) != 0 {
		t.Fatalf("the first look said something: %v %v", err, inboxRows(t, l))
	}
	if err := in.Scan(d, emit); err != nil || len(inboxRows(t, l)) != 0 {
		t.Fatalf("nothing changed: %v", inboxRows(t, l))
	}
	// an agent's item arrives, then is read
	request(t, d, 3, t0+5, "amy", "bob", "do x\nmore")
	in.Scan(d, emit)
	got := inboxRows(t, l)
	var item map[string]any
	if len(got) != 1 || got[0].Type != "inbox.added" || *got[0].Agent != "bob" || *got[0].Title != "do x" || string(got[0].Target) != `{"id":"3","screen":"inbox"}` ||
		json.Unmarshal(got[0].Data, &item) != nil || item["id"] != float64(3) || item["read"] != false {
		t.Fatalf("%+v %s", got, got[0].Data)
	}
	exec(t, d, "UPDATE requests SET read_at = ?, state_at = ? WHERE id = 3", t0+9, t0+9)
	in.Scan(d, emit)
	if got = inboxRows(t, l); len(got) != 2 || got[1].Type != "inbox.changed" || !json.Valid(got[1].Data) {
		t.Fatalf("%+v", got)
	}
	// the founder's inbox: an item arrives for them
	request(t, d, 4, t0+10, "amy", "human", "decide?")
	in.Scan(d, emit)
	if got = inboxRows(t, l); len(got) != 3 || got[2].Type != "inbox.added" || *got[2].Agent != "amy" {
		t.Fatalf("%+v", got)
	}
	// a cursor moves: the item is read
	exec(t, d, "INSERT INTO cursors(name, row_id, updated) VALUES('for_you_seen:amy', 99, 0)")
	in.Scan(d, emit)
	got = inboxRows(t, l)
	if len(got) != 5 { // items 2 and 4 both turn read
		t.Fatalf("%d rows: %+v", len(got), got)
	}
	// the founder's inbox is rebuilt only when dirty: a change nothing touched waits for the next touch of one of their requests
	request(t, d, 6, t0+12, "amy", "bob", "for bob")
	in.Scan(d, emit)
	if len(inboxRows(t, l)) != 6 {
		t.Fatalf("%d rows", len(inboxRows(t, l)))
	}
	exec(t, d, "UPDATE requests SET body = 'edited quietly' WHERE id = 4")
	in.Scan(d, emit)
	if len(inboxRows(t, l)) != 6 {
		t.Fatalf("the founder's inbox was rebuilt with nothing of theirs touched")
	}
	request(t, d, 5, t0+13, "amy", "human", "another")
	in.Scan(d, emit)
	if got = inboxRows(t, l); len(got) != 8 { // item 5 arrives and the quiet edit of item 4 is told with it
		t.Fatalf("%d rows", len(got))
	}
	// an item leaves the founder's inbox: answered
	exec(t, d, "UPDATE requests SET state = 'answered', state_at = ? WHERE id = 5", t0+20)
	in.Scan(d, emit)
	got = inboxRows(t, l)
	last := got[len(got)-1]
	json.Unmarshal(last.Data, &item)
	if len(got) != 9 || last.Type != "inbox.changed" || item["id"] != float64(5) || item["read"] != true || item["answered"] != false {
		t.Fatalf("%d rows: %+v %s", len(got), last, last.Data)
	}
}

func TestAFirstLookThatFailsIsTakenAgainAndStillSaysNothing(t *testing.T) {
	d, l := rig(t)
	for i := int64(1); i <= 50; i++ {
		request(t, d, i, t0-100, "amy", "bob", "old")
	}
	request(t, d, 51, t0-100, "amy", "human", "old for you")
	in := &Inbox{Reads: &reads.Env{DB: d}, Now: func() time.Time { return time.Unix(t0, 0) }}
	emit := func(e stream.Entry) (*stream.Event, error) { return l.Append(e) }
	dead, err := store.Open(filepath.Join(t.TempDir(), "dead.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	dead.Close()
	if err := in.Scan(dead, emit); err == nil {
		t.Fatal("a first look on a closed database succeeded")
	}
	if err := in.Scan(d, emit); err != nil || len(inboxRows(t, l)) != 0 {
		t.Fatalf("the retried first look said something: %v %d rows", err, len(inboxRows(t, l)))
	}
}

func TestGoAndPythonWriteTheSameInboxEvents(t *testing.T) {
	now := time.Now().Unix()
	py, events := parity(t, "_inbox", func(d *sql.DB) Detector { return &Inbox{Reads: &reads.Env{DB: d}} },
		func(run func(string, ...any)) {
			run("INSERT INTO requests(id, ts, requester, target, body, state, edge_ok) VALUES(1, ?, 'amy', 'bob', 'old', 'queued', 1)", now-100)
			run("INSERT INTO requests(id, ts, requester, target, body, state, edge_ok) VALUES(2, ?, 'amy', 'human', 'old for you', 'queued', 1)", now-100)
		},
		func(run func(string, ...any)) {
			run("INSERT INTO requests(id, ts, requester, target, body, kind, state, edge_ok) VALUES(3, ?, 'amy', 'bob', 'do x', 'task', 'queued', 1)", now+100)
			run("INSERT INTO requests(id, ts, requester, target, body, state, edge_ok) VALUES(4, ?, 'cat', 'human', 'decide?', 'queued', 1)", now+100)
			run("UPDATE requests SET read_at = ?, state_at = ? WHERE id = 1", now+101, now+101)
			run("INSERT INTO cursors(name, row_id, updated) VALUES('for_you_seen:amy', 99, 0)")
			run("UPDATE requests SET state = 'answered', state_at = ? WHERE id = 4", now+102)
		})
	if len(py) != len(events) || len(py) == 0 {
		t.Fatalf("python wrote %d rows, go %d", len(py), len(events))
	}
	for i, e := range events {
		sameRow(t, i, py[i], e)
		if !same(py[i]["data"], e.Data) {
			t.Errorf("row %d data: python %v, go %s", i, py[i]["data"], e.Data)
		}
	}
}
