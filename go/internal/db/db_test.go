package db

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/store"
)

func open(t *testing.T) Q {
	t.Helper()
	dir, err := os.MkdirTemp("", "db")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	d, err := store.Open(filepath.Join(dir, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	Now = func() int64 { return 1000 }
	t.Cleanup(func() { Now = defaultNow })
	return d
}

func TestEventsKeepKeyOrderAndDedup(t *testing.T) {
	q := open(t)
	id, err := AddEvent(q, "a", "ask", KV{"to", "b"}, KV{"n", 2})
	if err != nil || id == 0 {
		t.Fatal(id, err)
	}
	var p string
	q.QueryRow("SELECT payload FROM events WHERE id = ?", id).Scan(&p)
	if p != `{"to": "b", "n": 2}` {
		t.Fatal(p)
	}
	if id, _ := AddEventDeduped(q, "a", "k", "once"); id == 0 {
		t.Fatal("first dedup write lost")
	}
	if id, _ := AddEventDeduped(q, "a", "k", "once"); id != 0 {
		t.Fatal("second dedup write landed")
	}
}

func TestMetaAndCursors(t *testing.T) {
	q := open(t)
	if _, ok, _ := Meta(q, "x"); ok {
		t.Fatal("unset meta reads as set")
	}
	SetMeta(q, "taskhold:1", "v")
	SetMeta(q, "taskhold:2", "")
	SetMeta(q, "other", "v")
	names, _ := MetaNames(q, "taskhold:")
	if len(names) != 2 {
		t.Fatal(names)
	}
	if v, ok, _ := Meta(q, "taskhold:1"); v != "v" || !ok {
		t.Fatal(v, ok)
	}
	ClearMeta(q, "taskhold:1")
	if _, ok, _ := Meta(q, "taskhold:1"); ok {
		t.Fatal("cleared")
	}
	if n, _ := Cursor(q, "c"); n != 0 {
		t.Fatal(n)
	}
	SetCursor(q, "c", 7)
	if n, _ := Cursor(q, "c"); n != 7 {
		t.Fatal(n)
	}
	if at, ok, _ := CursorUpdated(q, "c"); !ok || at != 1000 {
		t.Fatal(at, ok)
	}
}

func TestToldAheadDropsMarksOutsideTheLine(t *testing.T) {
	q := open(t)
	MarkToldAhead(q, "a", 5, []int64{5})
	MarkToldAhead(q, "a", 6, []int64{6})
	if ok, _ := ToldAhead(q, "a", 5); ok {
		t.Fatal("a request that left the line stays marked")
	}
	if ok, _ := ToldAhead(q, "a", 6); !ok {
		t.Fatal("mark lost")
	}
	SetCursor(q, "notify:a", 3)
	ForgetTold(q, "a")
	if ok, _ := ToldAhead(q, "a", 6); ok {
		t.Fatal("forget_told left a mark")
	}
	if _, ok, _ := CursorUpdated(q, "notify:a"); ok {
		t.Fatal("forget_told left the notify cursor")
	}
}

func TestAgentStateWritesCreateIdleRowsAndKeepRunning(t *testing.T) {
	q := open(t)
	SetTerminal(q, "a", "%1", "/w")
	s, _ := GetAgentState(q, "a")
	if s == nil || s.State != "IDLE" || s.Terminal.String != "%1" {
		t.Fatalf("%+v", s)
	}
	SetAgentState(q, "a", "RUNNING", 0, "")
	if ok, _ := SetStateUnlessRunning(q, "a", "PARKED", "x"); ok {
		t.Fatal("overwrote RUNNING")
	}
	SetAgentState(q, "a", "PARKED", 50, "by hand")
	if due, _ := DueAgents(q, 49); len(due) != 0 {
		t.Fatal("not yet due")
	}
	if due, _ := DueAgents(q, 50); len(due) != 1 {
		t.Fatal("due")
	}
	BumpCounter(q, "a", "failed_attempts", 2)
	BumpCounter(q, "a", "failed_attempts", 1)
	if s, _ := GetAgentState(q, "a"); s.FailedAttempts != 3 {
		t.Fatal(s.FailedAttempts)
	}
	if BumpCounter(q, "a", "state", 1) == nil {
		t.Fatal("any column bumped")
	}
	SetClosed(q, "a", "sess")
	if s, _ := GetAgentState(q, "a"); !s.ClosedAt.Valid || s.ResumeSession.String != "sess" {
		t.Fatalf("%+v", s)
	}
	ClearClosed(q, "a")
	if s, _ := GetAgentState(q, "a"); s.ClosedAt.Valid || s.ResumeSession.Valid {
		t.Fatalf("%+v", s)
	}
	q.Exec("INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(9, 't', 'a', 'human', 1)")
	SetCurrentTask(q, "a", 9, true)
	if n, ok, _ := CurrentTask(q, "a"); n != 9 || !ok {
		t.Fatal(n, ok)
	}
	SetCurrentTask(q, "a", 0, false)
	if _, ok, _ := CurrentTask(q, "a"); ok {
		t.Fatal("not cleared")
	}
	SetExtraWorktrees(q, "a", []string{"/x", "é"})
	if s, _ := GetAgentState(q, "a"); s.ExtraWorktrees.String != `["/x", "\u00e9"]` {
		t.Fatal(s.ExtraWorktrees.String)
	}
}
