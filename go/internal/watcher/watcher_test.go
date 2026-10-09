package watcher

import (
	"database/sql"
	"encoding/json"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/stream"
)

func rig(t *testing.T) (*sql.DB, *stream.Log) {
	t.Helper()
	d, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	return d, &stream.Log{DB: d}
}

func exec(t *testing.T, d *sql.DB, q string, args ...any) {
	t.Helper()
	if _, err := d.Exec(q, args...); err != nil {
		t.Fatal(err)
	}
}

func rows(t *testing.T, l *stream.Log) []*stream.Event {
	t.Helper()
	got, err := l.Since(0, []string{"tasks"}, "", 100)
	if err != nil {
		t.Fatal(err)
	}
	return got
}

func TestTheFirstLookSaysNothingAndEachChangeIsOneTaskChangedRow(t *testing.T) {
	d, l := rig(t)
	exec(t, d, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'old', 'bob', 'human', 1)")
	tk := &Tasks{Reads: &reads.Env{DB: d}}
	emit := func(e stream.Entry) (*stream.Event, error) { return l.Append(e) }
	if err := tk.Scan(d, emit); err != nil || len(rows(t, l)) != 0 {
		t.Fatalf("the first look said something: %v %v", err, rows(t, l))
	}
	if err := tk.Scan(d, emit); err != nil || len(rows(t, l)) != 0 {
		t.Fatalf("nothing changed: %v", rows(t, l))
	}
	exec(t, d, "UPDATE tasks SET title = 'new' WHERE id = 1")
	exec(t, d, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(2, 'second', 'carol', 'human', 1)")
	if err := tk.Scan(d, emit); err != nil {
		t.Fatal(err)
	}
	got := rows(t, l)
	if len(got) != 2 || got[0].Type != "task.changed" || *got[0].Agent != "bob" || *got[0].Title != "new" || string(got[0].Target) != `{"id":"1","screen":"tasks"}` {
		t.Fatalf("%+v", got)
	}
	var task map[string]any
	if json.Unmarshal(got[0].Data, &task) != nil || task["id"] != float64(1) || task["to"] != "bob" || task["state"] != "draft" || *got[1].Agent != "carol" {
		t.Fatalf("%v", task)
	}
	exec(t, d, "INSERT INTO task_memos(task_id, author, body, created_at) VALUES(1, 'bob', 'note', 5)")
	if err := tk.Scan(d, emit); err != nil || len(rows(t, l)) != 3 {
		t.Fatalf("a memo is a change: %v %d", err, len(rows(t, l)))
	}
}

func TestAnEndedTaskLeavesTheWatchAfterADayWithoutAnEvent(t *testing.T) {
	d, l := rig(t)
	now := time.Now()
	exec(t, d, "INSERT INTO tasks(id, title, owner, opened_by, created_at, done_evidence, done_at) VALUES(1, 't', 'bob', 'human', 1, 'e', ?)", now.Unix()-10)
	tk := &Tasks{Reads: &reads.Env{DB: d}, Now: func() time.Time { return now }}
	emit := func(e stream.Entry) (*stream.Event, error) { return l.Append(e) }
	tk.Scan(d, emit)
	tk.Now = func() time.Time { return now.Add(2 * 24 * time.Hour) }
	if err := tk.Scan(d, emit); err != nil || len(rows(t, l)) != 0 {
		t.Fatalf("%v %v", err, rows(t, l))
	}
}

type failing struct{}

func (failing) Name() string             { return "failing" }
func (failing) Scan(*sql.DB, Emit) error { return sql.ErrConnDone }

func TestAFailingDetectorDoesNotStopTheOthersAndTheWatchSeesAnotherConnectionsCommit(t *testing.T) {
	d, l := rig(t)
	var mu sync.Mutex
	var logged []string
	w := &Watcher{DB: d, Emit: func(e stream.Entry) (*stream.Event, error) { return l.Append(e) },
		Detectors: []Detector{failing{}, &Tasks{Reads: &reads.Env{DB: d}}},
		Log:       func(f string, a ...any) { mu.Lock(); logged = append(logged, f); mu.Unlock() }}
	stop := make(chan struct{})
	done := make(chan struct{})
	go func() { w.Run(stop); close(done) }()
	time.Sleep(2 * Every) // the first look
	exec(t, d, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 't', 'bob', 'human', 1)")
	for i := 0; i < 100 && len(rows(t, l)) == 0; i++ {
		time.Sleep(20 * time.Millisecond)
	}
	time.Sleep(3 * Every)
	close(stop)
	<-done
	mu.Lock()
	defer mu.Unlock()
	if n := len(rows(t, l)); n != 1 || len(logged) == 0 {
		t.Fatalf("%d rows, %d failures logged", n, len(logged))
	}
}

func TestPrimeStartsANewCursorAtTheNewestRowAndKeepsAnOldOne(t *testing.T) {
	d, _ := rig(t)
	exec(t, d, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(7, 't', 'bob', 'human', 1)")
	got, err := Prime(d, "stream:test", "SELECT COALESCE(MAX(id), 0) FROM tasks")
	if err != nil || got != 7 {
		t.Fatalf("%d %v", got, err)
	}
	if err := db.SetCursor(d, "stream:test", 3); err != nil {
		t.Fatal(err)
	}
	if got, _ := Prime(d, "stream:test", "SELECT 99"); got != 3 {
		t.Fatalf("a cursor that is set moved: %d", got)
	}
}
