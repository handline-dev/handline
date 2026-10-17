package reads

import (
	"encoding/json"
	"testing"
)

func TestEachReadOfARequestCarriesItsRef(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'a', 'bob', 'human', 10)")
	run(t, e, "INSERT INTO requests(id, ref, ts, requester, target, body, state, kind, edge_ok, delivered_at, typed_at, landed_at) VALUES"+
		"(1, 'aaaaaaaaaaa1', 10, 'human', 'bob', 'do it', 'landed', 'task', 1, 11, 11, 11), "+
		"(2, 'aaaaaaaaaaa2', 20, 'bob', 'human', 'which one?', 'queued', 'ask', 1, NULL, NULL, NULL)")
	run(t, e, "INSERT INTO task_requests(task_id, request_id) VALUES(1, 1)")
	run(t, e, `INSERT INTO events(ts, agent, kind, payload) VALUES(30, 'human', 'task_handoff', '{"task_id": 1, "to": "bob", "note": "n", "request_id": 1}')`)
	asJSON := func(v any) map[string]any {
		b, _ := json.Marshal(v)
		var out map[string]any
		json.Unmarshal(b, &out)
		return out
	}

	task, err := e.taskGet(nil, map[string]any{"task": json.Number("1")})
	if err != nil {
		t.Fatal(err)
	}
	if r := asJSON(task)["requests"].([]any)[0].(map[string]any); r["ref"] != "aaaaaaaaaaa1" {
		t.Errorf("task.get requests: %v", r)
	}

	msgs, err := e.chatMessages(nil, map[string]any{"agent": "bob", "scope": "mine"})
	if err != nil {
		t.Fatal(err)
	}
	refs := map[float64]any{}
	for _, m := range asJSON(msgs)["messages"].([]any) {
		refs[m.(map[string]any)["id"].(float64)] = m.(map[string]any)["ref"]
	}
	if refs[1] != "aaaaaaaaaaa1" || refs[2] != "aaaaaaaaaaa2" {
		t.Errorf("chat.messages: %v", refs)
	}

	items, err := e.chatRequests(nil, map[string]any{"agent": "bob", "scope": "mine"})
	if err != nil {
		t.Fatal(err)
	}
	handoff := false
	for _, it := range asJSON(items)["items"].([]any) {
		if m := it.(map[string]any); m["type"] == "task_event" && m["request"] == float64(1) {
			handoff = m["request_ref"] == "aaaaaaaaaaa1"
		}
	}
	if !handoff {
		t.Errorf("chat.requests: no handoff item with the delivery's ref: %v", asJSON(items))
	}

	inbox, err := e.FounderItems(0)
	if err != nil {
		t.Fatal(err)
	}
	if len(inbox) != 1 || inbox[0]["ref"] != "aaaaaaaaaaa2" {
		t.Errorf("inbox items: %v", inbox)
	}
	got := read(t, e, asFounder, 2)
	if got["ref"] != "aaaaaaaaaaa2" {
		t.Errorf("request.read: %v", got)
	}
}
