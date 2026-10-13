package reads

import (
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/attachments"
)

func TestAMessageToAStructuredAgentCarriesItsReceiptAndLandsAsItSays(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	for _, q := range []string{
		"INSERT INTO requests(id, ts, requester, target, body, state, kind) VALUES(1, 1, 'human', 'bob', 'held', 'queued', 'say')",
		"INSERT INTO requests(id, ts, requester, target, body, state, kind) VALUES(2, 2, 'human', 'bob', 'written', 'queued', 'say')",
		"INSERT INTO requests(id, ts, requester, target, body, state, kind) VALUES(3, 3, 'human', 'bob', 'echoed', 'queued', 'say')",
		"INSERT INTO requests(id, ts, requester, target, body, state, kind) VALUES(4, 4, 'human', 'bob', 'lost', 'queued', 'say')",
		"INSERT INTO requests(id, ts, requester, target, body, state, kind) VALUES(5, 5, 'human', 'bob', 'taken back', 'withdrawn', 'say')",
		"INSERT INTO requests(id, ts, requester, target, body, state, kind) VALUES(6, 6, 'human', 'bob', 'to a terminal', 'queued', 'say')",
		"INSERT INTO operations(id, kind, state, result_json, agent, created_at, updated_at) VALUES('send:bob:request:1', 'send', 'queued', '{\"uuid\":\"u1\"}', 'bob', 1, 1)",
		"INSERT INTO operations(id, kind, state, result_json, agent, created_at, updated_at) VALUES('send:bob:request:2', 'send', 'sent', '{\"uuid\":\"u2\"}', 'bob', 1, 1)",
		"INSERT INTO operations(id, kind, state, result_json, agent, created_at, updated_at) VALUES('send:bob:request:3', 'send', 'confirmed', '{\"uuid\":\"u3\"}', 'bob', 1, 1)",
		"INSERT INTO operations(id, kind, state, result_json, agent, created_at, updated_at) VALUES('send:bob:request:4', 'send', 'unknown', '{\"uuid\":\"u4\"}', 'bob', 1, 1)",
		"INSERT INTO operations(id, kind, state, result_json, agent, created_at, updated_at) VALUES('send:bob:request:5', 'send', 'withdrawn', '{\"uuid\":\"u5\"}', 'bob', 1, 1)",
	} {
		if _, err := e.DB.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
	got, err := e.chatMessages(nil, map[string]any{"agent": "bob", "scope": "mine"})
	if err != nil {
		t.Fatal(err)
	}
	b, _ := json.Marshal(got)
	var out struct {
		Messages []struct {
			ID            int64
			Landing       string
			SendingReason any `json:"sending_reason"`
			Receipt       map[string]any
		}
	}
	json.Unmarshal(b, &out)
	type want struct{ landing, receipt string }
	wants := map[int64]want{1: {"queued", "queued"}, 2: {"typed", "sent"}, 3: {"delivered", "confirmed"}, 4: {"typed", "unknown"}, 6: {"queued", ""}}
	if len(out.Messages) != len(wants) { // the taken-back message is not drawn
		t.Fatalf("%s", b)
	}
	for _, m := range out.Messages {
		w := wants[m.ID]
		state, _ := m.Receipt["state"].(string)
		if m.Landing != w.landing || state != w.receipt || (w.receipt != "" && m.Receipt["uuid"] != "u"+string(rune('0'+m.ID))) {
			t.Errorf("#%d: landing %s receipt %v, want %s %s", m.ID, m.Landing, m.Receipt, w.landing, w.receipt)
		}
		if m.ID == 4 && m.SendingReason != "unknown" {
			t.Errorf("a lost message says why it is not delivered: %v", m.SendingReason)
		}
	}
}

func TestChatTranscriptServesAToolCallWithItsOutput(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	for i, ev := range []struct{ kind, body string }{
		{"tool_use", `{"tool_use":{"id":"u1","tool":"Bash","input":{"command":"seq 1 10"}}}`},
		{"tool_result", `{"tool_result":{"tool_use_id":"u1","ok":true,"text":"1\n2\n","truncated":true,"dropped_lines":8}}`},
		{"tool_use", `{"tool_use":{"id":"u2","tool":"Bash","input":{"command":"true"}}}`},
	} {
		if _, err := e.DB.Exec(`INSERT INTO transcript_events(agent, seq, session, turn, at, kind, item, body) VALUES('bob', ?, 's', NULL, 1000, ?, NULL, ?)`, i+1, ev.kind, ev.body); err != nil {
			t.Fatal(err)
		}
	}
	got, err := e.chatTranscript(nil, map[string]any{"agent": "bob"})
	if err != nil {
		t.Fatal(err)
	}
	var tools []map[string]any
	for _, it := range got.(map[string]any)["items"].([]any) {
		if m := it.(map[string]any); m["kind"] == "tool" {
			tools = append(tools, m)
		}
	}
	if len(tools) != 2 || tools[0]["text"] != "seq 1 10" || tools[0]["output"] != "1\n2\n" || tools[0]["dropped_lines"] != int64(8) {
		t.Fatalf("%v", tools)
	}
	if _, has := tools[1]["output"]; has || tools[1]["text"] != "true" {
		t.Fatalf("a call with no result has no output: %v", tools[1])
	}
	if _, has := tools[1]["dropped_lines"]; has {
		t.Fatalf("%v", tools[1])
	}
}

func TestChatTranscriptListsTheConversationsAnAgentBeganAmongItsItems(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	for _, q := range []string{
		`INSERT INTO session_starts(id, agent, ts, session_id, cause, resumed, text, prompt) VALUES(1, 'bob', 1000, 's1', 'launch', 0, '', '')`,
		`INSERT INTO session_starts(id, agent, ts, session_id, cause, resumed, text, prompt) VALUES(2, 'bob', 1001, 's1', 'wake', 1, 'kept', '')`,
		`INSERT INTO session_starts(id, agent, ts, session_id, cause, resumed, text, prompt) VALUES(3, 'bob', 1003, 's2', 'context_full', 0, 'the handoff', '')`,
		`INSERT INTO session_starts(id, agent, ts, session_id, cause, resumed, text, prompt) VALUES(4, 'bob', 1004, NULL, 'start', 0, '', '')`,
		`INSERT INTO session_starts(id, agent, ts, session_id, cause, resumed, text, prompt) VALUES(5, 'ann', 1000, 's9', 'launch', 0, '', '')`,
		`INSERT INTO transcript_events(agent, seq, session, turn, at, kind, item, body) VALUES('bob', 1, 's1', NULL, 1002, 'tool_use', NULL, '{"tool_use":{"id":"u1","tool":"Bash","input":{"command":"true"}}}')`,
	} {
		if _, err := e.DB.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
	got, err := e.chatTranscript(nil, map[string]any{"agent": "bob"})
	if err != nil {
		t.Fatal(err)
	}
	var kinds []string
	var sessions []map[string]any
	for _, it := range got.(map[string]any)["items"].([]any) {
		m := it.(map[string]any)
		kinds = append(kinds, m["kind"].(string))
		if m["kind"] == "session" {
			sessions = append(sessions, m)
		}
	}
	// A resume is not a new conversation, and one whose session id is not known yet is not listed.
	if strings.Join(kinds, " ") != "session tool session" {
		t.Fatalf("%v", kinds)
	}
	first, second := sessions[0]["session"].(map[string]any), sessions[1]["session"].(map[string]any)
	if sessions[0]["id"] != int64(1000_400_001) || first["id"] != "s1" || first["cause"] != "launch" || first["handoff"] != nil || first["started"] != "1970-01-01T00:16:40Z" {
		t.Fatalf("%v", sessions[0])
	}
	if sessions[1]["id"] != int64(1003_400_003) || second["cause"] != "context_full" || second["handoff"] != "the handoff" {
		t.Fatalf("%v", sessions[1])
	}
	before, err := e.chatTranscript(nil, map[string]any{"agent": "bob", "before": json.Number(strconv.FormatInt(sessions[1]["id"].(int64), 10))})
	if err != nil {
		t.Fatal(err)
	}
	if n := len(before.(map[string]any)["items"].([]any)); n != 2 {
		t.Fatalf("the page before a session item holds what is older: %d", n)
	}
}

func TestAPageCarriesAtMostTheOutputBudgetAndSaysWhatItCut(t *testing.T) {
	big := strings.Repeat("x", 800<<10) + "\n"
	items := []map[string]any{
		{"id": int64(1), "output": big + "tail\n", "dropped_lines": int64(3)},
		{"id": int64(2), "output": big},
		{"id": int64(3), "output": big},
		{"id": int64(4), "kind": "reply"},
	}
	fitToolOutput(items)
	total := 0
	for _, it := range items {
		if o, ok := it["output"].(string); ok {
			total += len(o)
		}
	}
	if total > toolOutputBudget {
		t.Fatalf("%d bytes", total)
	}
	if items[2]["output"] != big || items[1]["output"] != big || items[1]["dropped_lines"] != nil {
		t.Fatal("the newest items keep what fits")
	}
	if o := items[0]["output"].(string); len(o) != toolOutputBudget-2*len(big) || items[0]["dropped_lines"] != int64(3+2) {
		t.Fatalf("the oldest is cut to what is left, and the count it came with grows by the lines left out: %d %v", len(o), items[0]["dropped_lines"])
	}
}

func TestPagingBackThroughToolCallsThatShareASecondServesEachOnce(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	for i, at := range []int{101, 102, 103, 104, 105, 105} { // parallel calls share a second
		body := fmt.Sprintf(`{"tool_use":{"id":"u%d","tool":"Bash","input":{"command":"c%d"}}}`, i, i)
		if _, err := e.DB.Exec(`INSERT INTO transcript_events(agent, seq, session, turn, at, kind, item, body) VALUES('bob', ?, 's', NULL, ?, 'tool_use', NULL, ?)`, i+1, at, body); err != nil {
			t.Fatal(err)
		}
	}
	var seen []string
	params := map[string]any{"agent": "bob", "limit": json.Number("2")}
	for pages := 0; pages < 5; pages++ {
		got, err := e.chatTranscript(nil, params)
		if err != nil {
			t.Fatal(err)
		}
		page := got.(map[string]any)
		items := page["items"].([]any)
		var texts []string
		for _, it := range items {
			texts = append(texts, it.(map[string]any)["text"].(string))
		}
		seen = append(texts, seen...)
		if page["more"] != true {
			break
		}
		params = map[string]any{"agent": "bob", "limit": json.Number("2"), "before": json.Number(strconv.FormatInt(items[0].(map[string]any)["id"].(int64), 10))}
	}
	if strings.Join(seen, " ") != "c0 c1 c2 c3 c4 c5" {
		t.Fatalf("%v", seen)
	}
}

func TestSinceServesEveryToolCallFromItsSecondUpToWhatAPageKeeps(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	for i := 0; i < 60; i++ { // more than a default page of 50 calls in one second
		body := fmt.Sprintf(`{"tool_use":{"id":"u%d","tool":"Bash","input":{"command":"c%d"}}}`, i, i)
		if _, err := e.DB.Exec(`INSERT INTO transcript_events(agent, seq, session, turn, at, kind, item, body) VALUES('bob', ?, 's', NULL, 500, 'tool_use', NULL, ?)`, i+1, body); err != nil {
			t.Fatal(err)
		}
	}
	got, err := e.chatTranscript(nil, map[string]any{"agent": "bob", "since": json.Number("500000000")})
	if err != nil {
		t.Fatal(err)
	}
	if n := len(got.(map[string]any)["items"].([]any)); n != 60 {
		t.Fatalf("%d of 60 calls", n)
	}
}

func TestAMessageCarriesItsImages(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	for _, q := range []string{
		"INSERT INTO requests(id, ts, requester, target, body, state, kind) VALUES(1, 1, 'human', 'bob', 'look', 'queued', 'say')",
		"INSERT INTO requests(id, ts, requester, target, body, state, kind) VALUES(2, 2, 'human', 'bob', 'plain', 'queued', 'say')",
	} {
		if _, err := e.DB.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
	img := attachments.Info{ID: "00000000000000aa", Name: "a.png", Mime: "image/png", Bytes: 70}
	if err := attachments.Record(e.DB, 1, []attachments.Info{img}); err != nil {
		t.Fatal(err)
	}
	got, err := e.chatMessages(nil, map[string]any{"agent": "bob", "scope": "mine"})
	if err != nil {
		t.Fatal(err)
	}
	b, _ := json.Marshal(got)
	var out struct {
		Messages []struct {
			ID          int64
			Attachments []attachments.Info
		}
	}
	json.Unmarshal(b, &out)
	if len(out.Messages) != 2 || len(out.Messages[0].Attachments)+len(out.Messages[1].Attachments) != 1 {
		t.Fatalf("%s", b)
	}
	for _, m := range out.Messages {
		if (m.ID == 1) != (len(m.Attachments) == 1) || strings.Contains(string(b), `"attachments":null`) {
			t.Fatalf("%s", b)
		}
	}
}

func TestATaskEventSaysWhatAnEditAndAHandoffChanged(t *testing.T) {
	ev := func(kind, payload string) map[string]any {
		item, _, ok := taskItemOf(row{"id": int64(1), "ts": int64(5), "kind": kind, "payload": payload})
		if !ok {
			t.Fatalf("%s is not shown", kind)
		}
		return item["task_event"].(map[string]any)
	}
	if got := ev("edit_task", `{"task_id": 7, "owner": {"old": "a", "new": "b"}, "priority": {"old": 2, "new": 1}}`); got["change"] != "assigned to b, priority 2 -> 1" || got["event"] != "changed" {
		t.Fatalf("%v", got)
	}
	if got := ev("edit_task", `{"task_id": 7, "title": {"old": "x", "new": "y"}}`); got["change"] != "title changed" {
		t.Fatalf("%v", got)
	}
	if got := ev("task_handoff", `{"task_id": 7, "to": "bob", "note": "n"}`); got["change"] != "handed to bob" {
		t.Fatalf("%v", got)
	}
	for _, kind := range []string{"task_new", "mark_task_done", "queue_task"} {
		if _, has := ev(kind, `{"task_id": 7, "done": "ok"}`)["change"]; has {
			t.Errorf("%s names a change it has not got", kind)
		}
	}
}
