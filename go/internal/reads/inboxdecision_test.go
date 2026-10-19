package reads

import "testing"

// A decision is a task of the founder's, closed with task.done on InboxItem.task: an item the mailbox calls a decision always names it.
func TestADecisionInboxItemAlwaysCarriesItsTask(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO requests(id, ref, ts, requester, target, body, state, kind, edge_ok) VALUES"+
		"(1, 'aaaaaaaaaaa1', 10, 'bob', 'human', 'approve the plan', 'queued', 'task', 1), "+
		"(2, 'aaaaaaaaaaa2', 20, 'bob', 'human', 'which one?', 'queued', 'ask', 1), "+
		"(3, 'aaaaaaaaaaa3', 30, 'amy', 'human', 'second card', 'queued', 'task', 1), "+
		"(4, 'aaaaaaaaaaa4', 40, 'cy', 'human', 'a task of bob''s', 'queued', 'task', 1)")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(7, 'decide', 'human', 'bob', 1), (8, 'decide too', 'human', 'amy', 1), "+
		"(9, 'not the founder''s', 'bob', 'cy', 1)")
	run(t, e, "INSERT INTO task_requests(task_id, request_id) VALUES(7, 1), (8, 3), (9, 4)")
	items, err := e.FounderItems(0)
	if err != nil {
		t.Fatal(err)
	}
	decisions := map[int64]any{}
	for _, it := range items {
		if it["kind"] == "decision" {
			if it["task"] == nil {
				t.Fatalf("a decision with no task: %v", it)
			}
			decisions[it["id"].(int64)] = it["task"]
		}
	}
	if len(decisions) != 2 || decisions[1] != int64(7) || decisions[3] != int64(8) {
		t.Fatalf("each card of a task the founder owns is a decision naming it, and nothing else is: %v", decisions)
	}
}
