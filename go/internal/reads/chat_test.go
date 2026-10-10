package reads

import (
	"encoding/json"
	"testing"
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
