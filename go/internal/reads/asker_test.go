package reads

import (
	"encoding/json"
	"strconv"
	"testing"

	"github.com/handline/handline/go/internal/rpc"
)

func TestATaskAnswersToTheFounderWhenARequestOfItsCameFromThem(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	for id, requester := range map[int64]string{1: "human", 2: "amy", 3: "amy", 4: "human"} {
		run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok) VALUES(?, ?, ?, 'bob', 'b', 'queued', 'task', 1)", id, id, requester)
	}
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'a', 'bob', 'amy', 1), (2, 'b', 'bob', 'amy', 1), (3, 'c', 'bob', 'cy', 1), (4, 'd', 'bob', 'cy', 1)")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, reporter, created_at) VALUES(5, 'e', 'bob', 'amy', 'dee', 1)")
	// task 1 has a founder's request among others; 2 only the opener's; 3 a request of amy's but is opened by cy; 4 has no request at all;
	// 5 has a founder's request too, but its reporter comes first
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok) VALUES(9, 9, 'amy', 'bob', 'b', 'queued', 'task', 1)")
	run(t, e, "INSERT INTO task_requests(task_id, request_id) VALUES(1, 9), (1, 1), (2, 2), (3, 3), (5, 4)")
	want := map[int64]string{1: "human", 2: "amy", 3: "cy", 4: "cy", 5: "dee"}
	for id, who := range want {
		got, err := e.taskGet(nil, map[string]any{"task": json.Number(strconv.FormatInt(id, 10))})
		if err != nil {
			t.Fatal(err)
		}
		if a := got.(map[string]any)["asker"]; a != who {
			t.Errorf("task.get T%d: asker %v, want %s", id, a, who)
		}
	}
	list, err := e.tasksList(&rpc.Conn{Caller: rpc.Caller{Role: "founder"}}, map[string]any{})
	if err != nil {
		t.Fatal(err)
	}
	seen := 0
	for _, it := range list.(map[string]any)["items"].([]any) {
		m := it.(map[string]any)
		if id, _ := m["id"].(int64); m["asker"] != want[id] {
			t.Errorf("tasks.list T%d: asker %v, want %s", id, m["asker"], want[id])
		}
		seen++
	}
	if seen != 5 {
		t.Errorf("listed %d tasks", seen)
	}
}
