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
