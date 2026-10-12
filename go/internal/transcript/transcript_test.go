package transcript

import (
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/attachments"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/structured"
)

func newSink(t *testing.T) (*Sink, *[]Event) {
	t.Helper()
	d, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	var mu sync.Mutex
	var got []Event
	clock := int64(1_000_000)
	s := &Sink{DB: d, Now: func() time.Time { clock++; return time.Unix(clock, 0) },
		Session: func(string) string { return "sess1" },
		Prompt: func(agent string, block int64) (map[string]any, int64, bool) {
			return map[string]any{"id": "tu1", "agent": agent, "kind": "permission"}, 1_000_000, block == 7
		},
		Publish: func(e Event) { mu.Lock(); got = append(got, e); mu.Unlock() },
		Err:     func(a string, err error) { t.Error(a, err) }}
	return s, &got
}

func emit(s *Sink, agent, typ string, data map[string]any) {
	s.Emit(agent, structured.Event{Type: typ, Data: data})
}

func TestEventsAreNumberedFromOneAndKeepTheirTurn(t *testing.T) {
	s, got := newSink(t)
	emit(s, "bob", "message", map[string]any{"uuid": "u1", "text": "do it"})
	emit(s, "bob", "assistant_text", map[string]any{"text": "ok"})
	emit(s, "bob", "tool_use", map[string]any{"id": "tu1", "name": "Bash", "input": map[string]any{"command": "ls"}})
	emit(s, "bob", "tool_result", map[string]any{"tool_use_id": "tu1", "content": []any{map[string]any{"type": "text", "text": "a\nb"}}, "is_error": false})
	emit(s, "bob", "state", map[string]any{"state": "running"}) // carries nothing a view draws
	emit(s, "bob", "turn_end", map[string]any{"subtype": "success", "cost_usd": 0.5, "duration_ms": float64(12), "usage": map[string]any{"input_tokens": float64(3), "output_tokens": float64(4)}})
	emit(s, "bob", "assistant_text", map[string]any{"text": "outside a turn"})
	emit(s, "alice", "assistant_text", map[string]any{"text": "other agent"})
	kinds := []string{"turn_start", "text", "tool_use", "tool_result", "turn_end", "text", "text"}
	if len(*got) != len(kinds)+0 {
		t.Fatalf("%d events", len(*got))
	}
	for i, e := range (*got)[:6] {
		if e.Kind != kinds[i] || e.Seq != int64(i+1) || e.Session != "sess1" {
			t.Fatalf("%d: %+v", i, e)
		}
	}
	if (*got)[6].Agent != "alice" || (*got)[6].Seq != 1 {
		t.Fatalf("seq is per agent: %+v", (*got)[6])
	}
	if (*got)[1].Turn != "u1" || (*got)[4].Turn != "u1" || (*got)[5].Turn != "" {
		t.Fatalf("turns: %+v", *got)
	}
	end := (*got)[4].Body["turn_end"].(map[string]any)
	if end["reason"] != "done" || end["usage"].(map[string]any)["input"] != int64(3) || end["duration_ms"] != int64(12) {
		t.Fatalf("%v", end)
	}
	if r := (*got)[3].Body["tool_result"].(map[string]any); r["text"] != "a\nb" || r["ok"] != true || r["truncated"] != false {
		t.Fatalf("%v", r)
	}
}

func TestOnlyTextAndPermissionCarryAnItemAndItIsTheChatItem(t *testing.T) {
	s, got := newSink(t)
	emit(s, "bob", "message", map[string]any{"uuid": "u1", "text": "x"})
	emit(s, "bob", "assistant_text", map[string]any{"text": "hello"})
	emit(s, "bob", "permission_request", map[string]any{"prompt": "tu1", "block": int64(7), "tool_use": "tu1", "tool": "Bash"})
	emit(s, "bob", "permission_answer", map[string]any{"prompt": "tu1", "tool_use": "tu1", "allow": true, "by": "casey"})
	emit(s, "bob", "error", map[string]any{"code": "exit", "text": "claude exited"})
	for _, e := range *got {
		switch e.Kind {
		case "text":
			want := ItemID(e.At, ItemReply, e.Seq*2)
			if e.Item == nil || *e.Item != want {
				t.Fatalf("text item %v want %d", e.Item, want)
			}
			replies, err := Replies(s.DB, "bob", 10)
			if err != nil || len(replies) != 1 || replies[0].ID != want || replies[0].Text != "hello" {
				t.Fatalf("%+v %v", replies, err)
			}
		case "permission":
			if e.Item == nil || *e.Item != ItemID(1_000_000, ItemPrompt, 7) {
				t.Fatalf("permission item %v", e.Item)
			}
		default:
			if e.Item != nil {
				t.Fatalf("%s has an item", e.Kind)
			}
		}
	}
	ans := (*got)[3].Body["permission_answer"].(map[string]any)
	if ans["by"] != "person" || ans["behavior"] != "allow" || ans["tool_use_id"] != "tu1" || ans["rule"] != nil {
		t.Fatalf("%v", ans)
	}
}

func TestAPermissionWithoutItsPromptRowIsNotInventedAndTakesNoSeq(t *testing.T) {
	s, got := newSink(t)
	emit(s, "bob", "permission_request", map[string]any{"prompt": "tu1", "block": int64(99), "tool_use": "tu1"})
	emit(s, "bob", "assistant_text", map[string]any{"text": "a"})
	if len(*got) != 1 || (*got)[0].Seq != 1 {
		t.Fatalf("%+v", *got)
	}
}

func TestPolicyAnswersAndEndsOfPrompts(t *testing.T) {
	s, got := newSink(t)
	emit(s, "bob", "permission_answer", map[string]any{"tool_use": "t1", "allow": false, "by": "policy", "rule": "guard", "said": "no rm"})
	emit(s, "bob", "permission_answer", map[string]any{"tool_use": "t2", "allow": false, "by": "closed", "said": "time"})
	a := (*got)[0].Body["permission_answer"].(map[string]any)
	if a["by"] != "policy" || a["rule"] != "guard" || a["message"] != "no rm" || a["behavior"] != "deny" {
		t.Fatalf("%v", a)
	}
	if (*got)[1].Body["permission_answer"].(map[string]any)["by"] != "closed" {
		t.Fatal((*got)[1].Body)
	}
}

func TestAToolResultIsCutAtACharacterBoundary(t *testing.T) {
	s, got := newSink(t)
	big := strings.Repeat("é", ToolResultMax) // two bytes each
	emit(s, "bob", "tool_result", map[string]any{"tool_use_id": "t", "content": big, "is_error": true})
	r := (*got)[0].Body["tool_result"].(map[string]any)
	text := r["text"].(string)
	if r["truncated"] != true || len(text) > ToolResultMax || len(text) < ToolResultMax-1 || r["ok"] != false {
		t.Fatalf("%d %v", len(text), r["truncated"])
	}
}

func TestReadPagesBySeqAndShowsAGap(t *testing.T) {
	s, _ := newSink(t)
	for i := 0; i < 10; i++ {
		emit(s, "bob", "assistant_text", map[string]any{"text": "t"})
	}
	n := func(v int64) *int64 { return &v }
	p, err := Read(s.DB, "bob", nil, nil, 3)
	if err != nil || len(p.Events) != 3 || p.Events[0].Seq != 8 || p.Events[2].Seq != 10 || !p.More || p.First != 1 || p.Last != 10 || p.Gap {
		t.Fatalf("%+v %v", p, err)
	}
	p, _ = Read(s.DB, "bob", nil, n(8), 3)
	if len(p.Events) != 3 || p.Events[0].Seq != 5 || p.Events[2].Seq != 7 || !p.More {
		t.Fatalf("%+v", p)
	}
	p, _ = Read(s.DB, "bob", n(0), nil, 2)
	if len(p.Events) != 2 || p.Events[0].Seq != 1 || !p.More || p.Gap {
		t.Fatalf("%+v", p)
	}
	p, _ = Read(s.DB, "bob", n(10), nil, 2)
	if len(p.Events) != 0 || p.More || p.Gap || p.Last != 10 {
		t.Fatalf("up to date: %+v", p)
	}
	s.DB.Exec("DELETE FROM transcript_events WHERE seq <= 4")
	p, _ = Read(s.DB, "bob", n(1), nil, 100)
	if !p.Gap || p.First != 5 || p.Events[0].Seq != 5 {
		t.Fatalf("%+v", p)
	}
	if p, _ = Read(s.DB, "bob", n(4), nil, 100); p.Gap {
		t.Fatal("since first-1 misses nothing")
	}
	emit(s, "bob", "assistant_text", map[string]any{"text": "after the cut"})
	if p, _ = Read(s.DB, "bob", nil, nil, 1); p.Events[0].Seq != 11 {
		t.Fatalf("a seq is never reused: %+v", p)
	}
	if p, _ = Read(s.DB, "nobody", nil, nil, 5); len(p.Events) != 0 || p.First != 0 || p.Last != 0 || p.More {
		t.Fatalf("%+v", p)
	}
}

func TestOldEventsAreDroppedPastKeep(t *testing.T) {
	s, _ := newSink(t)
	s.DB.Exec("INSERT INTO cursors(name, row_id, updated) VALUES('transcript_seq:bob', ?, 0)", Keep+498)
	s.DB.Exec("INSERT INTO transcript_events(agent, seq, session, at, kind, body) VALUES('bob', 10, 's', 1, 'text', '{}')")
	emit(s, "bob", "assistant_text", map[string]any{"text": "a"})
	emit(s, "bob", "assistant_text", map[string]any{"text": "b"}) // seq Keep+500: everything up to 500 goes
	p, _ := Read(s.DB, "bob", nil, nil, 5)
	if p.First != Keep+499 || p.Last != Keep+500 {
		t.Fatalf("first %d last %d", p.First, p.Last)
	}
}

func TestTurnStartNamesWhoSentIt(t *testing.T) {
	s, got := newSink(t)
	s.DB.Exec("INSERT INTO requests(id, ts, requester, target, state, body) VALUES(5, 1, 'alice', 'bob', 'queued', 'hi')")
	s.DB.Exec("INSERT INTO operations(id, kind, state, step, message, result_json, agent, created_at, updated_at) VALUES('send:bob:request:5', 'send', 'sent', 'now', 'hi', '{\"uuid\":\"u5\"}', 'bob', 1, 1)")
	emit(s, "bob", "message", map[string]any{"uuid": "u5", "text": "hi"})
	if f := (*got)[0].Body["turn_start"].(map[string]any)["from"]; f != "alice" {
		t.Fatal(f)
	}
}

func TestATurnCutOffByACrashEndsAndTheNextSessionStartsItsOwn(t *testing.T) {
	s, got := newSink(t)
	emit(s, "bob", "message", map[string]any{"uuid": "u1", "text": "one"})
	emit(s, "bob", "assistant_text", map[string]any{"text": "a"})
	emit(s, "bob", "error", map[string]any{"code": "exit", "text": "claude exited"})
	emit(s, "bob", "session_end", nil)
	emit(s, "bob", "message", map[string]any{"uuid": "u2", "text": "two"})
	emit(s, "bob", "assistant_text", map[string]any{"text": "b"})
	var kinds, turns []string
	for _, e := range *got {
		kinds, turns = append(kinds, e.Kind), append(turns, e.Turn)
	}
	want := "turn_start text error turn_end turn_start text"
	if strings.Join(kinds, " ") != want || strings.Join(turns, " ") != "u1 u1 u1 u1 u2 u2" {
		t.Fatalf("%v %v", kinds, turns)
	}
	if r := (*got)[3].Body["turn_end"].(map[string]any)["reason"]; r != "error" {
		t.Fatalf("a turn ended by a crash says %v", r)
	}
}

func TestAStoppedSessionEndsItsTurnAsInterrupted(t *testing.T) {
	s, got := newSink(t)
	emit(s, "bob", "message", map[string]any{"uuid": "u1", "text": "one"})
	emit(s, "bob", "session_end", nil)
	emit(s, "bob", "session_end", nil) // no turn open: nothing to end
	if len(*got) != 2 || (*got)[1].Body["turn_end"].(map[string]any)["reason"] != "interrupted" {
		t.Fatalf("%+v", *got)
	}
}

func TestANewSessionIdEndsTheTurnOfTheOldOne(t *testing.T) {
	s, got := newSink(t)
	sess := "s1"
	s.Session = func(string) string { return sess }
	emit(s, "bob", "message", map[string]any{"uuid": "u1", "text": "one"})
	sess = "s2" // the daemon restarted the agent and no end event came
	emit(s, "bob", "message", map[string]any{"uuid": "u2", "text": "two"})
	var kinds []string
	for _, e := range *got {
		kinds = append(kinds, e.Kind)
	}
	if strings.Join(kinds, " ") != "turn_start turn_end turn_start" || (*got)[2].Turn != "u2" || (*got)[1].Session != "s1" {
		t.Fatalf("%v %+v", kinds, *got)
	}
}

func TestOneAgentsSlowWriteDoesNotHoldUpAnothersEvents(t *testing.T) {
	s, got := newSink(t)
	hold, entered := make(chan struct{}), make(chan struct{})
	prev := s.Publish
	s.Publish = func(e Event) {
		if e.Agent == "slow" {
			close(entered)
			<-hold // the first agent's publish takes as long as it likes
			return
		}
		prev(e)
	}
	go emit(s, "slow", "message", map[string]any{"uuid": "u1", "text": "x"})
	<-entered
	done := make(chan struct{})
	go func() { emit(s, "quick", "message", map[string]any{"uuid": "u2", "text": "y"}); close(done) }()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("another agent's event waited for the slow one")
	}
	close(hold)
	if len(*got) == 0 {
		t.Fatal("the quick agent's event was published")
	}
}

func TestToolsPairACallWithItsResultAndCountTheLinesTheCutTook(t *testing.T) {
	s, _ := newSink(t)
	emit(s, "bob", "tool_use", map[string]any{"id": "a", "name": "Bash", "input": map[string]any{"command": "seq 1 9"}})
	emit(s, "bob", "tool_result", map[string]any{"tool_use_id": "a", "content": []any{map[string]any{"type": "text", "text": "1\n2\n"}}})
	emit(s, "bob", "tool_use", map[string]any{"id": "b", "name": "Read", "input": map[string]any{"file_path": "/x/y"}})
	big := strings.Repeat("x", ToolResultMax-1) + "\n\n\nend\n" // the first newline is the last byte kept; the three after it are cut
	emit(s, "bob", "tool_result", map[string]any{"tool_use_id": "b", "content": big})
	emit(s, "bob", "tool_use", map[string]any{"id": "c", "name": "Bash", "input": map[string]any{"command": "true"}})
	tools, err := Tools(s.DB, "bob", ToolWindow{Limit: 10})
	if err != nil || len(tools) != 3 {
		t.Fatalf("%v %v", tools, err)
	}
	a, b, c := tools[0], tools[1], tools[2] // oldest first
	if c.Text != "true" || c.Output != nil {
		t.Errorf("unpaired call: %+v", c)
	}
	if b.Name != "Read" || b.Text != "/x/y" || b.Output == nil || len(*b.Output) != ToolResultMax || b.Dropped != 3 {
		t.Errorf("cut result: %+v", b)
	}
	if a.Text != "seq 1 9" || a.Output == nil || *a.Output != "1\n2\n" || a.Dropped != 0 {
		t.Errorf("whole result: %+v", a)
	}
	if a.ID >= b.ID || b.ID >= c.ID {
		t.Errorf("ids not in call order: %d %d %d", a.ID, b.ID, c.ID)
	}
}

func TestACutThatLeavesNoNewlineStillCountsALineAndAnOldCutRowReadsAsCut(t *testing.T) {
	s, got := newSink(t)
	emit(s, "bob", "tool_use", map[string]any{"id": "a", "name": "Bash", "input": map[string]any{"command": "yes | tr -d '\\n'"}})
	emit(s, "bob", "tool_result", map[string]any{"tool_use_id": "a", "content": strings.Repeat("x", ToolResultMax+5000)}) // one line, no newline
	r := (*got)[1].Body["tool_result"].(map[string]any)
	if r["truncated"] != true || r["dropped_lines"] != 1 {
		t.Fatalf("%v", r)
	}
	emit(s, "bob", "tool_use", map[string]any{"id": "b", "name": "Bash", "input": map[string]any{"command": "old"}})
	if _, err := s.DB.Exec(`INSERT INTO transcript_events(agent, seq, session, turn, at, kind, item, body) VALUES('bob', 99, 's', NULL, 1000000, 'tool_result', NULL, ?)`,
		`{"tool_result":{"tool_use_id":"b","ok":true,"text":"cut","truncated":true}}`); err != nil {
		t.Fatal(err)
	}
	tools, err := Tools(s.DB, "bob", ToolWindow{Limit: 10})
	if err != nil || len(tools) != 2 {
		t.Fatalf("%v %v", tools, err)
	}
	if tools[1].Output == nil || tools[1].Dropped != 1 {
		t.Errorf("a row from before the count was kept reads as cut: %+v", tools[1])
	}
	if tools[0].Dropped != 1 || len(*tools[0].Output) != ToolResultMax {
		t.Errorf("%+v", tools[0])
	}
}

func TestToolsReadOnlyTheCallsTheWindowNeedsAndFitOutputSaysWhatItCut(t *testing.T) {
	s, _ := newSink(t) // the clock ticks a second per event: a call and its result are two seconds
	for _, id := range []string{"a", "b", "c", "d"} {
		emit(s, "bob", "tool_use", map[string]any{"id": id, "name": "Bash", "input": map[string]any{"command": id}})
		emit(s, "bob", "tool_result", map[string]any{"tool_use_id": id, "content": "out-" + id})
	}
	newest, _ := Tools(s.DB, "bob", ToolWindow{Limit: 2})
	if len(newest) != 2 || newest[0].Text != "c" || newest[1].Text != "d" || *newest[1].Output != "out-d" {
		t.Fatalf("the newest two, oldest first: %+v", newest)
	}
	from, _ := Tools(s.DB, "bob", ToolWindow{Since: ItemID(newest[0].At, 0, 0), Limit: 10})
	if len(from) != 2 || from[0].Text != "c" {
		t.Fatalf("from the second of c: %+v", from)
	}
	upTo, _ := Tools(s.DB, "bob", ToolWindow{Before: newest[0].ID, Limit: 10})
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
}

func TestTurnStartCarriesTheImagesOfTheMessage(t *testing.T) {
	s, got := newSink(t)
	s.DB.Exec("INSERT INTO requests(id, ts, requester, target, state, body) VALUES(5, 1, 'human', 'bob', 'queued', 'hi')")
	s.DB.Exec("INSERT INTO operations(id, kind, state, step, message, result_json, agent, created_at, updated_at) VALUES('send:bob:request:5', 'send', 'sent', 'now', 'hi', '{\"uuid\":\"u5\"}', 'bob', 1, 1)")
	img := attachments.Info{ID: "00000000000000aa", Name: "a.png", Mime: "image/png", Bytes: 70}
	if err := attachments.Record(s.DB, 5, []attachments.Info{img}); err != nil {
		t.Fatal(err)
	}
	emit(s, "bob", "message", map[string]any{"uuid": "u5", "text": "hi\n[image 1: /x/a.png (image/png, 70 B)]"})
	start := (*got)[0].Body["turn_start"].(map[string]any)
	list, _ := start["attachments"].([]any)
	if len(list) != 1 || list[0].(map[string]any)["id"] != img.ID {
		t.Fatalf("%v", start)
	}
}
