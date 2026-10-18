package reads

import "testing"

func TestACardTheDaemonRaisedAfterHealingCarriesItsFix(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	run(t, e, "INSERT INTO requests(id, ref, ts, requester, target, body, state, kind, edge_ok) VALUES"+
		"(1, 'aaaaaaaaaaa1', 10, 'bob', 'human', 'bob is stuck', 'queued', 'task', 1), "+
		"(2, 'aaaaaaaaaaa2', 20, 'bob', 'human', 'which one?', 'queued', 'ask', 1)")
	run(t, e, `INSERT INTO events(ts, agent, kind, payload) VALUES(10, 'bob', 'raise_decision', '{"request_id": 1, "task_id": 1, "reason": "heal", "fix": "restart_terminal"}')`)

	items, err := e.FounderItems(0)
	if err != nil {
		t.Fatal(err)
	}
	fixes := map[int64]any{}
	for _, it := range items {
		if fix, ok := it["fix"]; ok {
			fixes[it["id"].(int64)] = fix
		}
	}
	if len(fixes) != 1 || fixes[1] != "restart_terminal" {
		t.Fatalf("the healing card has its fix and an ask has none: %v", fixes)
	}
}
