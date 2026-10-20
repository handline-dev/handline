package reads

import (
	"encoding/json"
	"fmt"
	"strconv"
	"testing"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/taskresult"
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

func TestATaskGetCarriesWhatItsEndKeptAndWhetherItIsAUITask(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok) VALUES(1, 1, 'human', 'bob', 'b', 'queued', 'task', 1)")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'a', 'bob', 'human', 1), (2, 'plain', 'bob', 'human', 1)")
	run(t, e, "INSERT INTO task_requests(task_id, request_id) VALUES(1, 1)")
	if err := taskresult.MarkUI(e.DB, 1); err != nil {
		t.Fatal(err)
	}
	if err := taskresult.Save(e.DB, 1, taskresult.Result{Commit: "abc", OnMain: true, Shots: []taskresult.Shot{{N: 1, Bytes: 9}}}); err != nil {
		t.Fatal(err)
	}
	get := func(id string) map[string]any {
		got, err := e.taskGet(nil, map[string]any{"task": json.Number(id)})
		if err != nil {
			t.Fatal(err)
		}
		return got.(map[string]any)
	}
	one := get("1")
	res, _ := one["result"].(map[string]any)
	shots, _ := res["shots"].([]any)
	if one["ui"] != true || res["commit"] != "abc" || res["on_main"] != true || len(shots) != 1 || shots[0].(map[string]any)["bytes"] != int64(9) {
		t.Fatalf("%v", one)
	}
	if plain := get("2"); plain["result"] != nil || plain["ui"] != nil {
		t.Fatalf("a task with neither carries nothing extra: %v", plain)
	}
}

func TestATaskGetListsEachAssigneeAndOwnerChange(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'a', 'bob', 'human', 1)")
	run(t, e, `INSERT INTO events(ts, agent, kind, payload) VALUES
		(10, 'carol', 'edit_task', '{"task_id": 1, "owner": {"old": "al", "new": "bob"}, "reporter": {"old": "human", "new": "carol"}}'),
		(11, 'human', 'edit_task', '{"task_id": 1, "title": {"old": "x", "new": "a"}}'),
		(12, 'human', 'edit_task', '{"task_id": 2, "owner": {"old": "bob", "new": "al"}}')`)
	got, err := e.taskGet(nil, map[string]any{"task": json.Number("1")})
	if err != nil {
		t.Fatal(err)
	}
	as, _ := got.(map[string]any)["assignments"].([]any)
	if len(as) != 2 {
		t.Fatalf("one entry per changed field of this task's edits, none for a title or another task: %v", as)
	}
	a, o := as[0].(map[string]any), as[1].(map[string]any)
	if a["what"] != "assignee" || a["from"] != "al" || a["to"] != "bob" || a["by"] != "carol" ||
		o["what"] != "owner" || o["from"] != "human" || o["to"] != "carol" {
		t.Fatalf("%v", as)
	}
}

// my_turn is the founder's open tasks that wait on them, one row a task: an answered one, a draft, another's and an ended one are not in it,
// and a task with many messages on it is still one row.
func TestMyTurnListsTheOpenTasksAssignedToTheFounder(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, done_evidence) VALUES"+
		"(1, 'waits', 'human', 'amy', 1, 'amy', NULL), (3, 'draft', 'human', 'amy', 1, NULL, NULL), (4, 'amys', 'amy', 'human', 1, 'human', NULL), "+
		"(5, 'ended', 'human', 'amy', 1, 'amy', 'done')")
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok) VALUES"+
		"(1, 1, 'amy', 'human', 'waits', 'landed', 'task', 1), (2, 2, 'amy', 'human', 'more', 'landed', 'say', 1), (3, 3, 'amy', 'human', 'and more', 'landed', 'say', 1)")
	run(t, e, "INSERT INTO task_requests(task_id, request_id) VALUES(1, 1), (1, 2), (1, 3)")
	founder := &rpc.Conn{Caller: rpc.Caller{Role: "founder"}}
	list, err := e.tasksList(founder, map[string]any{"my_turn": true})
	if err != nil {
		t.Fatal(err)
	}
	var ids []any
	for _, it := range list.(map[string]any)["items"].([]any) {
		ids = append(ids, it.(map[string]any)["id"])
	}
	if fmt.Sprint(ids) != "[1]" {
		t.Fatalf("my turn: %v", ids)
	}
	if _, err := e.tasksList(founder, map[string]any{"my_turn": true, "owner": "amy"}); err == nil {
		t.Fatal("my_turn takes no owner")
	}
}
