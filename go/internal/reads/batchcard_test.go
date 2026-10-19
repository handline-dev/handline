package reads

import "testing"

// A lead's batch discard is one card in its own chat, listing each task with its title and reason; the tasks' own discards are not
// shown there again, and stay in their owners' chats.
func TestABatchDiscardIsOneCardInItsAuthorsChat(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "lead", "")
	addAgent(t, e, "w1", "")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'first', 'w1', 'lead', 1), (2, 'mine', 'lead', 'lead', 1)")
	run(t, e, `INSERT INTO events(id, ts, agent, kind, payload) VALUES`+
		`(10, 100, 'lead', 'discard_task', '{"task_id": 1, "reason": "merged", "batch": "b1"}'), `+
		`(11, 100, 'lead', 'discard_task', '{"task_id": 2, "reason": "moot", "batch": "b1"}'), `+
		`(12, 100, 'lead', 'discard_batch', '{"batch": "b1", "tasks": [{"task_id": 1, "title": "first", "reason": "merged", "withdrew": [5]}, `+
		`{"task_id": 2, "title": "mine", "reason": "moot", "withdrew": []}]}')`)

	kinds := func(agent string) map[string][]map[string]any {
		got, err := e.chatTranscript(nil, map[string]any{"agent": agent})
		if err != nil {
			t.Fatal(err)
		}
		out := map[string][]map[string]any{}
		for _, it := range got.(map[string]any)["items"].([]any) {
			m := it.(map[string]any)
			out[m["kind"].(string)] = append(out[m["kind"].(string)], m)
		}
		return out
	}
	lead := kinds("lead")
	if len(lead["task_batch"]) != 1 || len(lead["task_event"]) != 0 {
		t.Fatalf("one card and no single discard in the lead's chat: %v", lead)
	}
	card := lead["task_batch"][0]["task_batch"].(map[string]any)
	tasks := card["tasks"].([]any)
	if card["who"] != "lead" || len(tasks) != 2 || tasks[0].(map[string]any)["title"] != "first" || tasks[1].(map[string]any)["reason"] != "moot" {
		t.Fatalf("each task with its title and reason: %v", card)
	}
	if w1 := kinds("w1"); len(w1["task_event"]) != 1 || len(w1["task_batch"]) != 0 {
		t.Fatalf("w1's chat keeps its own task's discard: %v", w1)
	}

	if _, owner, ok, _ := e.TaskEventItem(12); !ok || owner != "lead" {
		t.Fatalf("the live card goes to the lead's chat: %q %v", owner, ok)
	}
	if _, _, ok, _ := e.TaskEventItem(11); ok {
		t.Fatal("the lead's own task is not sent to its chat apart from the card")
	}
	if _, owner, ok, _ := e.TaskEventItem(10); !ok || owner != "w1" {
		t.Fatalf("w1's task goes to w1's chat: %q %v", owner, ok)
	}
}
