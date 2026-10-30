package transcript

import (
	"database/sql"
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/store"
)

// rows is a database the tests write transcript rows into, one put at a time: seq counts up from 1 and at a second per row.
type rows struct {
	t   *testing.T
	DB  *sql.DB
	seq int64
}

func newRows(t *testing.T) *rows {
	t.Helper()
	d, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	return &rows{t: t, DB: d}
}

func (r *rows) put(agent, kind string, body map[string]any) {
	r.t.Helper()
	r.seq++
	b, _ := json.Marshal(body)
	if _, err := r.DB.Exec("INSERT INTO transcript_events(agent, seq, session, turn, at, kind, item, body) VALUES(?,?,?,NULL,?,?,NULL,?)",
		agent, r.seq, "s", 1_000_000+r.seq, kind, string(b)); err != nil {
		r.t.Fatal(err)
	}
}

func (r *rows) call(agent, id, name string, input map[string]any) {
	r.put(agent, "tool_use", map[string]any{"tool_use": map[string]any{"id": id, "tool": name, "input": input}})
}

func (r *rows) result(agent, id, text string, dropped int) {
	res := map[string]any{"tool_use_id": id, "ok": true, "text": text, "truncated": dropped > 0}
	if dropped > 0 {
		res["dropped_lines"] = dropped
	}
	r.put(agent, "tool_result", map[string]any{"tool_result": res})
}

func TestReadPagesBySeqAndShowsAGap(t *testing.T) {
	r := newRows(t)
	for i := 0; i < 10; i++ {
		r.put("bob", "text", map[string]any{"text": "t"})
	}
	n := func(v int64) *int64 { return &v }
	p, err := Read(r.DB, "bob", nil, nil, 3)
	if err != nil || len(p.Events) != 3 || p.Events[0].Seq != 8 || p.Events[2].Seq != 10 || !p.More || p.First != 1 || p.Last != 10 || p.Gap {
		t.Fatalf("%+v %v", p, err)
	}
	p, _ = Read(r.DB, "bob", nil, n(8), 3)
	if len(p.Events) != 3 || p.Events[0].Seq != 5 || p.Events[2].Seq != 7 || !p.More {
		t.Fatalf("%+v", p)
	}
	p, _ = Read(r.DB, "bob", n(0), nil, 2)
	if len(p.Events) != 2 || p.Events[0].Seq != 1 || !p.More || p.Gap {
		t.Fatalf("%+v", p)
	}
	p, _ = Read(r.DB, "bob", n(10), nil, 2)
	if len(p.Events) != 0 || p.More || p.Gap || p.Last != 10 {
		t.Fatalf("up to date: %+v", p)
	}
	r.DB.Exec("DELETE FROM transcript_events WHERE seq <= 4")
	p, _ = Read(r.DB, "bob", n(1), nil, 100)
	if !p.Gap || p.First != 5 || p.Events[0].Seq != 5 {
		t.Fatalf("%+v", p)
	}
	if p, _ = Read(r.DB, "bob", n(4), nil, 100); p.Gap {
		t.Fatal("since first-1 misses nothing")
	}
	if p, _ = Read(r.DB, "nobody", nil, nil, 5); len(p.Events) != 0 || p.First != 0 || p.Last != 0 || p.More {
		t.Fatalf("%+v", p)
	}
}

func TestToolsPairACallWithItsResultAndReadTheLinesTheCutTook(t *testing.T) {
	r := newRows(t)
	r.call("bob", "a", "Bash", map[string]any{"command": "seq 1 9"})
	r.result("bob", "a", "1\n2\n", 0)
	r.call("bob", "b", "Read", map[string]any{"file_path": "/x/y"})
	r.result("bob", "b", strings.Repeat("x", 10), 3)
	r.call("bob", "c", "Bash", map[string]any{"command": "true"})
	tools, err := Tools(r.DB, "bob", ToolWindow{Limit: 10})
	if err != nil || len(tools) != 3 {
		t.Fatalf("%v %v", tools, err)
	}
	a, b, c := tools[0], tools[1], tools[2] // oldest first
	if c.Text != "true" || c.Output != nil {
		t.Errorf("unpaired call: %+v", c)
	}
	if b.Name != "Read" || b.Text != "/x/y" || b.Output == nil || b.Dropped != 3 {
		t.Errorf("cut result: %+v", b)
	}
	if a.Text != "seq 1 9" || a.Output == nil || *a.Output != "1\n2\n" || a.Dropped != 0 {
		t.Errorf("whole result: %+v", a)
	}
	if a.ID >= b.ID || b.ID >= c.ID {
		t.Errorf("ids not in call order: %d %d %d", a.ID, b.ID, c.ID)
	}
}

func TestAnOldCutRowWithNoLineCountReadsAsCut(t *testing.T) {
	r := newRows(t)
	r.call("bob", "b", "Bash", map[string]any{"command": "old"})
	r.put("bob", "tool_result", map[string]any{"tool_result": map[string]any{"tool_use_id": "b", "ok": true, "text": "cut", "truncated": true}})
	tools, err := Tools(r.DB, "bob", ToolWindow{Limit: 10})
	if err != nil || len(tools) != 1 || tools[0].Output == nil || tools[0].Dropped != 1 {
		t.Fatalf("a row from before the count was kept reads as cut: %+v %v", tools, err)
	}
}

func TestToolsReadOnlyTheCallsTheWindowNeedsAndFitOutputSaysWhatItCut(t *testing.T) {
	r := newRows(t) // a second per row: a call and its result are two seconds
	for _, id := range []string{"a", "b", "c", "d"} {
		r.call("bob", id, "Bash", map[string]any{"command": id})
		r.result("bob", id, "out-"+id, 0)
	}
	newest, _ := Tools(r.DB, "bob", ToolWindow{Limit: 2})
	if len(newest) != 2 || newest[0].Text != "c" || newest[1].Text != "d" || *newest[1].Output != "out-d" {
		t.Fatalf("the newest two, oldest first: %+v", newest)
	}
	from, _ := Tools(r.DB, "bob", ToolWindow{Since: ItemID(newest[0].At, 0, 0), Limit: 10})
	if len(from) != 2 || from[0].Text != "c" {
		t.Fatalf("from the second of c: %+v", from)
	}
	upTo, _ := Tools(r.DB, "bob", ToolWindow{Before: newest[0].ID, Limit: 10})
	if len(upTo) != 2 || upTo[1].Text != "b" {
		t.Fatalf("below the id of c: %+v", upTo)
	}
	if kept, dropped := FitOutput("1\n2\n3\n", 4); kept != "1\n2\n" || dropped != 1 {
		t.Errorf("%q %d", kept, dropped)
	}
	if kept, dropped := FitOutput("1\n2\n", 0); kept != "" || dropped != 2 {
		t.Errorf("%q %d", kept, dropped)
	}
	if kept, dropped := FitOutput("whole", 10); kept != "whole" || dropped != 0 {
		t.Errorf("%q %d", kept, dropped)
	}
	if kept, dropped := FitOutput("é"+strings.Repeat("x", 3), 1); kept != "" || dropped != 1 {
		t.Errorf("a cut at a character boundary: %q %d", kept, dropped)
	}
}

// A reply item is its newest text event with that item id, of whichever agent; a non-text event with an item id is no reply.
func TestReplyByIDIsTheNewestTextOfThatItem(t *testing.T) {
	r := newRows(t)
	for _, row := range []struct {
		seq        int64
		kind, body string
		item       int64
	}{
		{1, "text", `{"text":"first"}`, 1000},
		{2, "text", `{"text":"then this"}`, 1000},
		{3, "tool_use", `{}`, 2000},
	} {
		if _, err := r.DB.Exec("INSERT INTO transcript_events(agent, seq, session, turn, at, kind, item, body) VALUES('bob', ?, 's', NULL, 100, ?, ?, ?)",
			row.seq, row.kind, row.item, row.body); err != nil {
			t.Fatal(err)
		}
	}
	got, agent, ok, err := ReplyByID(r.DB, 1000)
	if err != nil || !ok || agent != "bob" || got.ID != 1000 || got.Text != "then this" {
		t.Fatalf("%+v %q %v %v", got, agent, ok, err)
	}
	if _, _, ok, err := ReplyByID(r.DB, 2000); err != nil || ok {
		t.Fatalf("a tool call's item is no reply: %v %v", ok, err)
	}
	if mine, err := IsReply(r.DB, "bob", 2000); err != nil || mine {
		t.Fatalf("nor for IsReply: %v %v", mine, err)
	}
}
