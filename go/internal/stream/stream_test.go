package stream

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/store"
)

// wire is a connection's far end: every message the subscription wrote.
type wire struct {
	mu   sync.Mutex
	got  []map[string]any
	hung chan struct{}
	once sync.Once
}

func newWire() *wire { return &wire{hung: make(chan struct{})} }

func (w *wire) send(b []byte) bool {
	var m map[string]any
	if json.Unmarshal(b, &m) != nil {
		return false
	}
	w.mu.Lock()
	w.got = append(w.got, m)
	w.mu.Unlock()
	return true
}

func (w *wire) hangUp() { w.once.Do(func() { close(w.hung) }) }

// methods is the notices written so far, as "method:cursor" for an event and "method:reason" for a gap.
func (w *wire) methods() []string {
	w.mu.Lock()
	defer w.mu.Unlock()
	var out []string
	for _, m := range w.got {
		p := m["params"].(map[string]any)
		if m["method"] == "gap" {
			out = append(out, "gap:"+p["reason"].(string))
		} else {
			out = append(out, fmt.Sprintf("event:%d", int64(p["cursor"].(float64))))
		}
	}
	return out
}

func (w *wire) waitFor(t *testing.T, n int) []string {
	t.Helper()
	for i := 0; i < 200; i++ {
		if got := w.methods(); len(got) >= n {
			return got
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("wanted %d notices, got %v", n, w.methods())
	return nil
}

func rig(t *testing.T) (*Hub, *sql.DB) {
	t.Helper()
	d, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	h := NewHub()
	h.Log = &Log{DB: d}
	return h, d
}

func subscribe(t *testing.T, h *Hub, topics []string, agent string, since *int64, visible func(*Event) bool) (*wire, int64) {
	t.Helper()
	w := newWire()
	names := map[string]bool{}
	for _, n := range topics {
		names[n] = true
	}
	s := &sub{send: w.send, hangUp: w.hangUp, topics: names, agent: agent, visible: visible, logged: true}
	s.cond = sync.NewCond(&s.mu)
	top, err := h.add(s, since)
	if err != nil {
		t.Fatal(err)
	}
	go s.run()
	t.Cleanup(func() { h.remove(s) })
	return w, top
}

func add(t *testing.T, h *Hub, topic, typ, agent string) *Event {
	t.Helper()
	ev, err := h.Append(Entry{Topic: topic, Type: typ, Agent: agent, Data: map[string]any{"n": 1}})
	if err != nil {
		t.Fatal(err)
	}
	return ev
}

func TestASlowClientGetsAGapAndIsHungUp(t *testing.T) {
	w := newWire()
	s := &sub{send: w.send, hangUp: w.hangUp, topics: map[string]bool{"chat": true}}
	s.cond = sync.NewCond(&s.mu)
	for i := int64(1); i <= MaxQueue+1; i++ {
		ev := &Event{Cursor: i, Topic: "chat", Type: "chat.message", Data: json.RawMessage(`{}`)}
		s.offer(ev, msgOf(ev))
	}
	done := make(chan struct{})
	go func() { s.run(); close(done) }()
	<-done
	<-w.hung
	if got := w.methods(); len(got) != 1 || got[0] != "gap:slow_client" {
		t.Fatalf("%v", got)
	}
}

func TestAppendedRowsAreServedInCursorOrderToWhoWantsThem(t *testing.T) {
	h, _ := rig(t)
	w, top := subscribe(t, h, []string{"chat", "tasks"}, "", nil, nil)
	if top != 0 {
		t.Fatalf("top %d", top)
	}
	a := add(t, h, "chat", "chat.message", "bob")
	add(t, h, "hr", "hr.raised", "")
	c := add(t, h, "tasks", "task.changed", "bob")
	if err := h.poll(); err != nil {
		t.Fatal(err)
	}
	if got := w.waitFor(t, 2); got[0] != fmt.Sprintf("event:%d", a.Cursor) || got[1] != fmt.Sprintf("event:%d", c.Cursor) || len(got) != 2 {
		t.Fatalf("%v", got)
	}
	if err := h.poll(); err != nil || len(w.methods()) != 2 {
		t.Fatalf("a second poll sent again: %v %v", err, w.methods())
	}
}

func TestARowAnotherWriterAppendedIsServedToo(t *testing.T) {
	h, d := rig(t)
	w, _ := subscribe(t, h, []string{"agents"}, "", nil, nil)
	if _, err := (&Log{DB: d}).Append(Entry{Topic: "agents", Type: "agent.changed", Agent: "bob", Data: map[string]any{"name": "bob"}}); err != nil {
		t.Fatal(err)
	}
	if err := h.poll(); err != nil {
		t.Fatal(err)
	}
	w.waitFor(t, 1)
}

func TestAResumeReplaysWhatItMissedOnceAndThenGoesLive(t *testing.T) {
	h, _ := rig(t)
	a := add(t, h, "chat", "chat.message", "bob")
	b := add(t, h, "chat", "chat.message", "bob")
	w, top := subscribe(t, h, []string{"chat"}, "", &a.Cursor, nil)
	if top != b.Cursor {
		t.Fatalf("top %d", top)
	}
	c := add(t, h, "chat", "chat.message", "bob")
	if err := h.poll(); err != nil {
		t.Fatal(err)
	}
	got := w.waitFor(t, 2)
	if got[0] != fmt.Sprintf("event:%d", b.Cursor) || got[1] != fmt.Sprintf("event:%d", c.Cursor) {
		t.Fatalf("%v", got)
	}
}

func TestAResumeFromBeforeRetentionIsAGapAndTheConnectionStays(t *testing.T) {
	h, d := rig(t)
	a := add(t, h, "chat", "chat.message", "bob")
	b := add(t, h, "chat", "chat.message", "bob")
	if err := db.SetCursor(d, PrunedCursor, a.Cursor); err != nil {
		t.Fatal(err)
	}
	zero := int64(0)
	w, _ := subscribe(t, h, []string{"chat"}, "", &zero, nil)
	if got := w.waitFor(t, 1); got[0] != "gap:retention" {
		t.Fatalf("%v", got)
	}
	c := add(t, h, "chat", "chat.message", "bob")
	if err := h.poll(); err != nil {
		t.Fatal(err)
	}
	if got := w.waitFor(t, 2); got[1] != fmt.Sprintf("event:%d", c.Cursor) {
		t.Fatalf("%v (b=%d)", got, b.Cursor)
	}
}

func TestAResumeMoreThanReplayMaxBehindIsAGap(t *testing.T) {
	h, d := rig(t)
	tx, _ := d.Begin()
	for i := 0; i < ReplayMax+1; i++ {
		if _, err := tx.Exec("INSERT INTO stream_log(ts, topic, type, data) VALUES(1, 'chat', 'chat.message', '{}')"); err != nil {
			t.Fatal(err)
		}
	}
	tx.Commit()
	zero := int64(0)
	w, _ := subscribe(t, h, []string{"chat"}, "", &zero, nil)
	if got := w.waitFor(t, 1); len(got) != 1 || got[0] != "gap:retention" {
		t.Fatalf("%d notices, first %v", len(got), got[0])
	}
	zero = int64(1)
	w, _ = subscribe(t, h, []string{"chat"}, "", &zero, nil)
	if got := w.waitFor(t, ReplayMax); len(got) != ReplayMax {
		t.Fatalf("a resume %d behind was not replayed in full: %d", ReplayMax, len(got))
	}
}

func TestAnAgentFilterKeepsItsEventsAndThoseOfNoAgent(t *testing.T) {
	h, _ := rig(t)
	w, _ := subscribe(t, h, []string{"chat"}, "bob", nil, nil)
	add(t, h, "chat", "chat.message", "alice")
	b := add(t, h, "chat", "chat.message", "bob")
	n := add(t, h, "chat", "chat.message", "")
	if err := h.poll(); err != nil {
		t.Fatal(err)
	}
	got := w.waitFor(t, 2)
	if got[0] != fmt.Sprintf("event:%d", b.Cursor) || got[1] != fmt.Sprintf("event:%d", n.Cursor) || len(got) != 2 {
		t.Fatalf("%v", got)
	}
}

func TestAnAgentSessionIsSentWhatItsRoleMayRead(t *testing.T) {
	h, _ := rig(t)
	reach := func(string) (map[string]bool, error) { return map[string]bool{"bob": true, "carol": true}, nil }
	w, _ := subscribe(t, h, []string{"inbox", "tasks", "agents", "chat", "notifications"}, "", nil, newAgentView("bob", reach).may)
	add(t, h, "inbox", "inbox.added", "alice") // another's inbox
	i := add(t, h, "inbox", "inbox.added", "bob")
	add(t, h, "tasks", "task.changed", "dave") // outside its reach
	k := add(t, h, "tasks", "task.changed", "carol")
	add(t, h, "chat", "chat.message", "bob") // a member's topic
	add(t, h, "notifications", "prompt_asked", "bob")
	n := add(t, h, "notifications", "stall", "carol")
	if err := h.poll(); err != nil {
		t.Fatal(err)
	}
	want := []string{fmt.Sprintf("event:%d", i.Cursor), fmt.Sprintf("event:%d", k.Cursor), fmt.Sprintf("event:%d", n.Cursor)}
	got := w.waitFor(t, 3)
	time.Sleep(50 * time.Millisecond)
	if got = w.methods(); fmt.Sprint(got) != fmt.Sprint(want) {
		t.Fatalf("%v, want %v", got, want)
	}
}

func TestLiveEventsOfNoCursorAreNotDeduplicated(t *testing.T) {
	h, _ := rig(t)
	w := newWire()
	s := &sub{send: w.send, hangUp: w.hangUp, topics: map[string]bool{devicesTopic: true}}
	s.cond = sync.NewCond(&s.mu)
	if _, err := h.add(s, nil); err != nil {
		t.Fatal(err)
	}
	go s.run()
	t.Cleanup(func() { h.remove(s) })
	h.Emit("device.paired", map[string]any{"id": "a"}, nil, "")
	h.Emit("device.revoked", map[string]any{"id": "a"}, nil, "")
	w.waitFor(t, 2)
}

func TestPruneDropsWhatIsPastRetentionAndRemembersHowFar(t *testing.T) {
	h, d := rig(t)
	now := time.Now()
	l := &Log{DB: d, Now: func() time.Time { return now.Add(-RetentionS*time.Second - time.Hour) }}
	old, _ := l.Append(Entry{Topic: "chat", Type: "chat.message", Data: map[string]any{}})
	fresh := add(t, h, "chat", "chat.message", "")
	if err := h.Log.Prune(); err != nil {
		t.Fatal(err)
	}
	if got, _ := h.Log.PrunedThrough(); got != old.Cursor {
		t.Fatalf("pruned through %d, want %d", got, old.Cursor)
	}
	rows, _ := h.Log.Since(0, []string{"chat"}, "", 10)
	if len(rows) != 1 || rows[0].Cursor != fresh.Cursor {
		t.Fatalf("%v", rows)
	}
}

func TestAnAccountChangeIsAnAccountsEventWithTheAccountAsData(t *testing.T) {
	h, _ := rig(t)
	if err := h.AccountChanged(map[string]any{"name": "main", "login": map[string]any{"state": "waiting"}}); err != nil {
		t.Fatal(err)
	}
	rows, err := h.Log.Since(0, []string{"accounts"}, "", 10)
	if err != nil || len(rows) != 1 {
		t.Fatalf("%v %v", rows, err)
	}
	e := rows[0]
	if e.Type != "account.changed" || e.Agent != nil || string(e.Data) != `{"login":{"state":"waiting"},"name":"main"}` {
		t.Fatalf("%+v %s", e, e.Data)
	}
}
