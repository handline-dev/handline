package watcher

import (
	"database/sql"
	"encoding/json"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/cmdruns"
	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/stream"
	"github.com/handline/handline/go/internal/term"
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

func TestAReplyToTheFoundersMessageIsAMessageOfItsOwn(t *testing.T) {
	r := newChatRig(t)
	c := r.chat()
	r.scan(c)
	exec(t, r.d, "INSERT INTO requests(id, ts, requester, target, body, state, kind) VALUES(1, 100, 'human', 'bob', 'q', 'landed', 'say')")
	r.scan(c)
	wantTypes(t, r.types(), "chat.message:bob")
	exec(t, r.d, "INSERT INTO requests(id, ts, requester, target, body, state, kind, parent_id) VALUES(2, 150, 'bob', 'human', 'the answer', 'landed', 'say', 1)")
	r.scan(c)
	wantTypes(t, r.types(), "chat.message:bob", "chat.message:bob")
	var m map[string]any
	if json.Unmarshal(r.rows()[1].Data, &m) != nil || m["id"] != float64(2) || m["parent"] != float64(1) {
		t.Fatalf("%s", r.rows()[1].Data)
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

// A closed prompt's item says its answer: what prompt.answer kept on the block, or, answered in the terminal, outside with when it closed.
func TestAClosedPromptCarriesItsAnswer(t *testing.T) {
	r := newChatRig(t)
	ins := "INSERT INTO prompt_blocks(agent, kind, fingerprint, title, panel, detail, answerable, first_seen, last_seen) VALUES(?, 'question', 'f', 't', '[]', '{}', 1, 1, 1)"
	exec(t, r.d, ins, "amy")
	exec(t, r.d, ins, "bob")
	c := r.chat()
	r.scan(c)
	exec(t, r.d, "UPDATE prompt_blocks SET answered_at = 7, answer_ok = 1, answer_label = 'Other', answer_note = 'main', answer_by = 'casey' WHERE agent = 'amy'")
	exec(t, r.d, "UPDATE prompt_blocks SET gone_at = 9")
	r.scan(c)
	answers := map[string]string{}
	for _, e := range r.rows() {
		var it map[string]any
		json.Unmarshal(e.Data, &it)
		a, _ := json.Marshal(it["answer"])
		answers[*e.Agent] = string(a)
	}
	if want := `{"at":"1970-01-01T00:00:07Z","by":"casey","label":"Other","note":"main"}`; answers["amy"] != want {
		t.Fatalf("answered in Handline: %s", answers["amy"])
	}
	if want := `{"at":"1970-01-01T00:00:09Z","outside":true}`; answers["bob"] != want {
		t.Fatalf("answered in the terminal: %s", answers["bob"])
	}
}

func TestAConversationIsToldOnceWhenItsSessionIdIsKnown(t *testing.T) {
	r := newChatRig(t)
	now := time.Now().Unix()
	ins := "INSERT INTO session_starts(agent, ts, session_id, cause, resumed, text, prompt) VALUES(?, ?, ?, ?, ?, '', '')"
	exec(t, r.d, ins, "amy", now, nil, "launch", 0) // written before the daemon, bound after it: told when it is bound
	c := r.chat()
	r.scan(c)
	exec(t, r.d, ins, "bob", now, "s1", "wake", 0)      // a structured start knows its session at once
	exec(t, r.d, ins, "bob", now, "s1", "wake", 1)      // a resume begins nothing
	exec(t, r.d, ins, "cat", now, nil, "start", 0)      // a terminal launch waits for its hook
	exec(t, r.d, ins, "dan", now-3600, nil, "start", 0) // past the bind window: never bound
	r.scan(c)
	wantTypes(t, r.types(), "chat.transcript:bob")
	exec(t, r.d, "UPDATE session_starts SET session_id = 's2' WHERE agent = 'cat'")
	exec(t, r.d, "UPDATE session_starts SET session_id = 's3' WHERE agent = 'amy'")
	r.scan(c)
	r.scan(c)
	wantTypes(t, r.types(), "chat.transcript:bob", "chat.transcript:amy", "chat.transcript:cat")
	var it map[string]any
	json.Unmarshal(r.rows()[2].Data, &it)
	if it["kind"] != "session" || it["session"].(map[string]any)["id"] != "s2" || it["session"].(map[string]any)["cause"] != "start" {
		t.Fatalf("%v", it)
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

func TestARunStartingAndEndingIsTheMessageSaidAgainWithItsRuns(t *testing.T) {
	r := newChatRig(t)
	exec(t, r.d, "INSERT INTO requests(id, ts, requester, target, body, state, kind) VALUES(1, 100, 'bob', 'human', 'run it', 'landed', 'say')")
	started := cmdruns.Now() - 10
	panes := map[string]term.PaneRun{}
	c := r.chat()
	c.Panes = func() map[string]term.PaneRun { return panes }
	r.scan(c)
	wantTypes(t, r.types())
	run, err := cmdruns.Start(r.d, cmdruns.Block{Message: 1, N: 0}, "%4", started)
	if err != nil {
		t.Fatal(err)
	}
	panes["%4"] = term.PaneRun{Tag: run.Tag()}
	r.scan(c)
	r.scan(c)
	wantTypes(t, r.types(), "chat.message:bob")
	panes["%4"] = term.PaneRun{Tag: run.Tag(), Dead: true, Status: 1, DeadTime: started + 3}
	r.scan(c)
	r.scan(c)
	wantTypes(t, r.types(), "chat.message:bob", "chat.message:bob")
	var m struct {
		Runs []map[string]any `json:"runs"`
	}
	rows := r.rows()
	if json.Unmarshal(rows[1].Data, &m) != nil || len(m.Runs) != 1 || m.Runs[0]["state"] != "failed" || m.Runs[0]["exit"] != float64(1) || m.Runs[0]["seconds"] != float64(3) {
		t.Fatalf("%s", rows[1].Data)
	}
}

func TestARunKeptBeforeTheFirstLookIsNotNews(t *testing.T) {
	r := newChatRig(t)
	exec(t, r.d, "INSERT INTO requests(id, ts, requester, target, body, state, kind) VALUES(1, 100, 'bob', 'human', 'run it', 'landed', 'say')")
	cmdruns.Start(r.d, cmdruns.Block{Message: 1, N: 0}, "%4", cmdruns.Now())
	c := r.chat()
	r.scan(c)
	r.scan(c)
	wantTypes(t, r.types())
}

// A run of a reply item's block starting and ending sends that item again as chat.transcript, to its agent, with its runs.
func TestARunOfAReplysBlockIsTheItemSaidAgainWithItsRuns(t *testing.T) {
	r := newChatRig(t)
	exec(t, r.d, `INSERT INTO transcript_events(agent, seq, session, turn, at, kind, item, body) VALUES('bob', 1, 's', NULL, 100, 'text', 1000, '{"text":"run make"}')`)
	// A message with the same id as the item: the reply's run is not its news.
	exec(t, r.d, "INSERT INTO requests(id, ts, requester, target, body, state, kind) VALUES(1000, 100, 'bob', 'human', 'same id', 'landed', 'say')")
	started := cmdruns.Now() - 10
	panes := map[string]term.PaneRun{}
	c := r.chat()
	c.Panes = func() map[string]term.PaneRun { return panes }
	r.scan(c)
	wantTypes(t, r.types())
	run, err := cmdruns.Start(r.d, cmdruns.Block{Message: 1000, N: 0, Reply: true}, "%4", started)
	if err != nil {
		t.Fatal(err)
	}
	panes["%4"] = term.PaneRun{Tag: run.Tag()}
	r.scan(c)
	wantTypes(t, r.types(), "chat.transcript:bob")
	panes["%4"] = term.PaneRun{Tag: run.Tag(), Dead: true, Status: 0, DeadTime: started + 2}
	r.scan(c)
	wantTypes(t, r.types(), "chat.transcript:bob", "chat.transcript:bob")
	var item struct {
		ID   int64            `json:"id"`
		Kind string           `json:"kind"`
		Text string           `json:"text"`
		Runs []map[string]any `json:"runs"`
	}
	rows := r.rows()
	if json.Unmarshal(rows[1].Data, &item) != nil || item.ID != 1000 || item.Kind != "reply" || item.Text != "run make" || len(item.Runs) != 1 ||
		item.Runs[0]["block"] != "r1000:0" || item.Runs[0]["state"] != "done" || item.Runs[0]["exit"] != float64(0) {
		t.Fatalf("%s", rows[1].Data)
	}
}
