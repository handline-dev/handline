package taskwrites

import "testing"

func TestAnAgentCannotGiveATaskToTheFounder(t *testing.T) {
	db, mk := discardEnv(t)
	exec(t, db, "INSERT INTO tasks(id, title, owner, opened_by, created_at, sent_at) VALUES(1, 'build', 'worker', 'manager', 1, 1)")
	refused := func(r map[string]any) bool {
		e, _ := r["error"].(map[string]any)
		return e != nil && e["message"] == askFounderInstead
	}
	r := call(t, mk("worker", &fakeTeller{}),
		`{"jsonrpc":"2.0","id":2,"method":"task.handoff","params":{"task":1,"to":"human","note":"n","request_id":"h1"}}`,
		`{"jsonrpc":"2.0","id":3,"method":"task.new","params":{"owner":"human","title":"d","time_box":"1h","request_id":"n1"}}`,
		`{"jsonrpc":"2.0","id":4,"method":"task.edit","params":{"task":1,"owner":"human","request_id":"e1"}}`)
	for i := 0; i < 3; i++ {
		if !refused(r[i]) {
			t.Errorf("call %d: %v", i, r[i])
		}
	}
	if count(t, db, "SELECT COUNT(*) FROM tasks WHERE owner = 'human'") != 0 || count(t, db, "SELECT COUNT(*) FROM tasks WHERE id = 1 AND owner = 'worker'") != 1 {
		t.Fatal("a refused give leaves the task with its owner")
	}
}
