package watcher

import (
	"database/sql"
	"fmt"
	"strings"
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
	exec(t, d, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(7, 't', 'bob', 'human', 1)")
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
	if got[0].Type != "task_done" || string(got[0].Data) != `{"from_founder":true,"reporter":"human","task":7,"task_title":"t"}` || *got[0].Title != "task 7 done" || string(got[0].Target) != `{"id":"7","screen":"task"}` || *got[0].Agent != "bob" {
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
	exec(t, d, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 't', 'bob', 'human', 1)")
	exec(t, d, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(2, 't', 'bob', 'human', 1)")
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
	if len(got) != 3 || string(got[1].Data) != `{"from_founder":true,"reporter":"human","task":2,"task_title":"t"}` || got[2].Type != "prompt_asked" {
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
		exec(t, d, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(?, 't', 'bob', 'human', 1)", i)
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
	if len(got) != 3 || string(got[0].Data) != `{"from_founder":true,"reporter":"human","task":1,"task_title":"t"}` || string(got[1].Data) != `{"from_founder":true,"reporter":"human","task":2,"task_title":"t"}` || string(got[2].Data) != `{"from_founder":true,"reporter":"human","task":3,"task_title":"t"}` {
		t.Fatalf("%d rows", len(got))
	}
}

func TestGoAndPythonWriteTheSameNotifications(t *testing.T) {
	py, events := parity(t, func(d *sql.DB) Detector { return &Notifications{Reads: &reads.Env{DB: d}} },
		func(run func(string, ...any)) {
			run("INSERT INTO events(ts, agent, kind, payload) VALUES(1, 'bob', 'mark_task_done', '{\"task_id\": 1}')")
			run("INSERT INTO prompt_blocks(agent, kind, fingerprint, title, panel, detail, first_seen, last_seen) VALUES('bob', 'permission', 'old', 'old', '[]', '{}', 1, 1)")
		},
		func(run func(string, ...any)) {
			run("INSERT INTO events(ts, agent, account, kind, payload) VALUES(5, 'amy', 'main', 'login_expired', 'not json')")
			run("INSERT INTO events(ts, agent, account, kind, payload) VALUES(6, 'amy', 'main', 'login_expired', '{\"account\": \"alt\"}')")
			run("INSERT INTO prompt_blocks(agent, kind, fingerprint, title, panel, detail, first_seen, last_seen) VALUES('amy', 'permission', 'fp1', 'Allow ls?', '[\"run ls?\"]', '{\"tool\":\"Bash\",\"command\":\"ls\",\"reason\":\"r\"}', 5, 9)")
			run("INSERT INTO prompt_blocks(agent, kind, fingerprint, title, panel, detail, first_seen, last_seen) VALUES('cat', 'question', 'fp2', 'Pick one', '[\"a\",\"b\"]', '{\"options\":[{\"label\":\"x\"},{\"label\":\"y\",\"checked\":true}],\"multi_select\":true}', 6, 9)")
		})
	if len(py) != len(events) || len(py) != 4 {
		t.Fatalf("python wrote %d rows, go %d", len(py), len(events))
	}
	for i, e := range events {
		sameRow(t, i, py[i], e)
		if !same(py[i]["data"], e.Data) {
			t.Errorf("row %d data: python %v, go %s", i, py[i]["data"], e.Data)
		}
	}
}

func TestAnAccountStalledOnAnotherErrorSaysTheErrorNotALogin(t *testing.T) {
	d, l := rig(t)
	emit := func(e stream.Entry) (*stream.Event, error) { return l.Append(e) }
	n := &Notifications{Reads: &reads.Env{DB: d}}
	if err := n.Scan(d, emit); err != nil {
		t.Fatal(err)
	}
	exec(t, d, "INSERT INTO events(ts, agent, account, kind, payload) VALUES(1, 'amy', 'gamma', 'login_expired', '{\"account\": \"gamma\", \"error\": \"oauth_org_not_allowed\"}')")
	exec(t, d, "INSERT INTO events(ts, agent, account, kind, payload) VALUES(2, 'bob', 'main', 'login_expired', '{\"account\": \"main\", \"error\": \"authentication_failed\"}')")
	if err := n.Scan(d, emit); err != nil {
		t.Fatal(err)
	}
	got := notes(t, l)
	if len(got) != 2 || *got[0].Title != "gamma stalled: oauth_org_not_allowed" || *got[1].Title != "main needs logging in" {
		t.Fatalf("%+v", got)
	}
}

func TestATaskDoneCarriesTheTasksTitleAndTheFirstLineOfItsEvidence(t *testing.T) {
	d, l := rig(t)
	emit := func(e stream.Entry) (*stream.Event, error) { return l.Append(e) }
	n := &Notifications{Reads: &reads.Env{DB: d}}
	if err := n.Scan(d, emit); err != nil {
		t.Fatal(err)
	}
	long := strings.Repeat("é", resultCap+10)
	tasks := []struct {
		title    string
		evidence any
	}{{"task 7", "built and tested\nPR 12, check green"}, {"task 8", nil}, {"task 9", long}, {"", "\n\n  first  \r\nsecond"}}
	for i, task := range tasks {
		id := 7 + i
		exec(t, d, "INSERT INTO tasks(id, title, owner, opened_by, done_evidence, created_at) VALUES(?, ?, 'bob', 'human', ?, 1)", id, task.title, task.evidence)
		exec(t, d, "INSERT INTO events(ts, agent, kind, payload) VALUES(?, 'bob', 'mark_task_done', ?)", id, fmt.Sprintf(`{"task_id": %d}`, id))
	}
	exec(t, d, "INSERT INTO events(ts, agent, kind, payload) VALUES(20, 'bob', 'mark_task_done', '{\"task_id\": 99}')") // no such task: not the founder's
	if err := n.Scan(d, emit); err != nil {
		t.Fatal(err)
	}
	want := []string{
		`{"from_founder":true,"reporter":"human","result":"built and tested","task":7,"task_title":"task 7"}`,
		`{"from_founder":true,"reporter":"human","task":8,"task_title":"task 8"}`,
		`{"from_founder":true,"reporter":"human","result":"` + long[:2*resultCap] + `","task":9,"task_title":"task 9"}`,
		`{"from_founder":true,"reporter":"human","result":"first","task":10}`,
	}
	got := notes(t, l)
	if len(got) != len(want) {
		t.Fatalf("%d notices", len(got))
	}
	for i, e := range got {
		if string(e.Data) != want[i] {
			t.Errorf("notice %d: %s", i, e.Data)
		}
	}
}

func TestADiskLowEventIsOneNotificationWithTheFreeSpace(t *testing.T) {
	d, l := rig(t)
	emit := func(e stream.Entry) (*stream.Event, error) { return l.Append(e) }
	n := &Notifications{Reads: &reads.Env{DB: d}}
	if err := n.Scan(d, emit); err != nil {
		t.Fatal(err)
	}
	exec(t, d, "INSERT INTO events(ts, agent, kind, payload) VALUES(1, '', 'disk_low', '{\"free_bytes\": 9663676416, \"total_bytes\": 494384795648}')")
	for range 2 {
		if err := n.Scan(d, emit); err != nil {
			t.Fatal(err)
		}
	}
	got := notes(t, l)
	if len(got) != 1 || string(got[0].Data) != `{"free_bytes":9663676416,"total_bytes":494384795648}` || *got[0].Title != "disk low: 9 GB left" {
		t.Fatalf("%+v", got)
	}
}

func TestATaskDoneSaysWhoTheTaskAnswersToAndWhetherTheFounderGaveIt(t *testing.T) {
	d, l := rig(t)
	emit := func(e stream.Entry) (*stream.Event, error) { return l.Append(e) }
	n := &Notifications{Reads: &reads.Env{DB: d}}
	if err := n.Scan(d, emit); err != nil {
		t.Fatal(err)
	}
	for i, q := range []string{
		"(1, 't', 'bob', 'amy', NULL)",    // an agent's task: it answers to its opener
		"(2, 't', 'bob', 'amy', 'human')", // an agent opened it for the founder, who answers for it
		"(3, 't', 'bob', 'human', 'amy')", // the founder opened it and an agent answers for it
		"(4, 't', 'bob', 'human', NULL)",
	} {
		exec(t, d, "INSERT INTO tasks(id, title, owner, opened_by, reporter, created_at) VALUES"+q[:len(q)-1]+", 1)")
		exec(t, d, "INSERT INTO events(ts, agent, kind, payload) VALUES(?, 'bob', 'mark_task_done', ?)", 10+i, fmt.Sprintf(`{"task_id": %d}`, i+1))
	}
	if err := n.Scan(d, emit); err != nil {
		t.Fatal(err)
	}
	want := []string{
		`{"from_founder":true,"reporter":"human","task":2,"task_title":"t"}`,
		`{"from_founder":true,"reporter":"human","task":4,"task_title":"t"}`,
	}
	got := notes(t, l)
	if len(got) != len(want) {
		t.Fatalf("%d notices", len(got))
	}
	for i, e := range got {
		if string(e.Data) != want[i] {
			t.Errorf("notice %d: %s, want %s", i, e.Data, want[i])
		}
	}
}
