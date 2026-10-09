package watcher

import (
	"database/sql"
	"encoding/json"
	"testing"

	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/stream"
)

type chatRig struct {
	t    *testing.T
	d    *sql.DB
	l    *stream.Log
	emit Emit
}

func newChatRig(t *testing.T) *chatRig {
	d, l := rig(t)
	return &chatRig{t, d, l, func(e stream.Entry) (*stream.Event, error) { return l.Append(e) }}
}

func (r *chatRig) chat() *Chat { return &Chat{Reads: &reads.Env{DB: r.d}} }

func (r *chatRig) scan(c *Chat) {
	r.t.Helper()
	if err := c.Scan(r.d, r.emit); err != nil {
		r.t.Fatal(err)
	}
}

func (r *chatRig) rows() []*stream.Event {
	r.t.Helper()
	got, err := r.l.Since(0, []string{"chat"}, "", 100)
	if err != nil {
		r.t.Fatal(err)
	}
	return got
}

func (r *chatRig) types() []string {
	var out []string
	for _, e := range r.rows() {
		out = append(out, e.Type+":"+*e.Agent)
	}
	return out
}

func wantTypes(t *testing.T, got []string, want ...string) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("got %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("got %v, want %v", got, want)
		}
	}
}

func TestAChangedRequestIsOneChatMessageToEachAgentOfItAndNotToTheFounder(t *testing.T) {
	r := newChatRig(t)
	exec(t, r.d, "INSERT INTO requests(id, ts, requester, target, body, state, kind) VALUES(1, 100, 'human', 'bob', 'old', 'queued', 'say')")
	c := r.chat()
	r.scan(c)
	wantTypes(t, r.types()) // the first look says nothing, not even for the newest second
	exec(t, r.d, "INSERT INTO requests(id, ts, requester, target, body, state, kind) VALUES(2, 200, 'amy', 'bob', 'hello', 'queued', 'say')")
	r.scan(c)
	r.scan(c)
	wantTypes(t, r.types(), "chat.message:bob", "chat.message:amy")
	got := r.rows()[0]
	var m map[string]any
	if json.Unmarshal(got.Data, &m) != nil || m["id"] != float64(2) || m["from"] != "amy" || m["body"] != "hello" || string(got.Target) != `{"id":"bob","screen":"agent"}` || got.Title != nil {
		t.Fatalf("%s", got.Data)
	}
	exec(t, r.d, "UPDATE requests SET state = 'landed', state_at = 300 WHERE id = 2")
	r.scan(c)
	wantTypes(t, r.types(), "chat.message:bob", "chat.message:amy", "chat.message:bob", "chat.message:amy")
}

func TestAnAnswerToTheFoundersMessageIsAMessageAndAReplyItem(t *testing.T) {
	r := newChatRig(t)
	c := r.chat()
	r.scan(c)
	exec(t, r.d, "INSERT INTO requests(id, ts, requester, target, body, state, kind) VALUES(1, 100, 'human', 'bob', 'q', 'landed', 'say')")
	r.scan(c)
	wantTypes(t, r.types(), "chat.message:bob")
	exec(t, r.d, "INSERT INTO requests(id, ts, requester, target, body, state, kind, settles) VALUES(2, 150, 'bob', 'human', 'the answer', 'landed', 'say', 1)")
	r.scan(c)
	got := r.types()
	if len(got) < 2 || got[1] != "chat.message:bob" {
		t.Fatalf("the settled request is said again: %v", got)
	}
	for _, e := range r.rows()[2:] {
		if e.Type != "chat.transcript" || *e.Agent != "bob" {
			t.Fatalf("%v", got)
		}
	}
}

func TestARestartSaysNothingAgainButAChangeWhileDownIsSaid(t *testing.T) {
	r := newChatRig(t)
	exec(t, r.d, "INSERT INTO requests(id, ts, requester, target, body, state, kind) VALUES(1, 100, 'human', 'bob', 'a', 'queued', 'say')")
	c := r.chat()
	r.scan(c)
	exec(t, r.d, "INSERT INTO requests(id, ts, requester, target, body, state, kind) VALUES(2, 200, 'human', 'bob', 'b', 'queued', 'say')")
	r.scan(c)
	wantTypes(t, r.types(), "chat.message:bob")
	again := r.chat() // the daemon restarted: its memory is gone, the cursor is not
	r.scan(again)
	r.scan(again)
	wantTypes(t, r.types(), "chat.message:bob")
	exec(t, r.d, "INSERT INTO requests(id, ts, requester, target, body, state, kind) VALUES(3, 300, 'human', 'bob', 'c', 'queued', 'say')")
	r.scan(again)
	wantTypes(t, r.types(), "chat.message:bob", "chat.message:bob")
}

func TestATaskEventOfAnOwnedTaskIsOneTranscriptItemAndTheFoundersIsNone(t *testing.T) {
	r := newChatRig(t)
	exec(t, r.d, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 't', 'bob', 'human', 1), (2, 'u', 'human', 'bob', 1)")
	c := r.chat()
	r.scan(c)
	for _, id := range []int{1, 2} {
		exec(t, r.d, "INSERT INTO events(ts, agent, kind, payload) VALUES(5, 'bob', 'task_new', ?)", `{"task_id":`+string(rune('0'+id))+`}`)
	}
	exec(t, r.d, "INSERT INTO events(ts, agent, kind, payload) VALUES(6, 'bob', 'other_kind', '{\"task_id\":1}')")
	r.scan(c)
	r.scan(c)
	wantTypes(t, r.types(), "chat.transcript:bob")
	var item map[string]any
	json.Unmarshal(r.rows()[0].Data, &item)
	if item["kind"] != "task_event" || item["task_event"].(map[string]any)["event"] != "opened" {
		t.Fatalf("%v", item)
	}
}

func TestAPromptIsToldWhenItOpensAndOnceMoreWhenItCloses(t *testing.T) {
	r := newChatRig(t)
	ins := "INSERT INTO prompt_blocks(agent, kind, fingerprint, title, panel, detail, answerable, first_seen, last_seen) VALUES(?, 'question', 'f', 't', '[]', '{}', 1, 1, 1)"
	exec(t, r.d, ins, "amy") // open before the daemon: told closed when it closes
	c := r.chat()
	r.scan(c)
	exec(t, r.d, ins, "bob")
	r.scan(c)
	wantTypes(t, r.types(), "chat.transcript:bob")
	exec(t, r.d, "UPDATE prompt_blocks SET gone_at = 9 WHERE id = 2")
	exec(t, r.d, "UPDATE prompt_blocks SET gone_at = 9 WHERE id = 1")
	r.scan(c)
	r.scan(c)
	wantTypes(t, r.types(), "chat.transcript:bob", "chat.transcript:amy", "chat.transcript:bob")
	var open []any
	for _, e := range r.rows() {
		var it map[string]any
		json.Unmarshal(e.Data, &it)
		open = append(open, it["open"])
	}
	if open[0] != true || open[1] != false || open[2] != false {
		t.Fatalf("%v", open)
	}
}

func TestAnAgentsWorkingFlagIsToldWhenItFlipsAndNotWhenItDoesNot(t *testing.T) {
	r := newChatRig(t)
	exec(t, r.d, "INSERT INTO agent_view(agent, view, updated) VALUES('bob', ?, 0)", `{"name":"bob","state":"idle"}`)
	c := r.chat()
	r.scan(c)
	exec(t, r.d, "UPDATE agent_view SET view = ? WHERE agent = 'bob'", `{"name":"bob","state":"running","state_since":"x"}`)
	r.scan(c)
	r.scan(c)
	wantTypes(t, r.types(), "chat.working:bob")
	var w map[string]any
	json.Unmarshal(r.rows()[0].Data, &w)
	if w["turn_running"] != true || w["since"] != "x" {
		t.Fatalf("%v", w)
	}
	exec(t, r.d, "UPDATE agent_view SET view = ? WHERE agent = 'bob'", `{"name":"bob","state":"running","state_since":"x","other":1}`)
	r.scan(c)
	wantTypes(t, r.types(), "chat.working:bob")
}
