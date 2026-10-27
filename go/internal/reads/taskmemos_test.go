package reads

import (
	"encoding/json"
	"fmt"
	"testing"
)

func memoIDs(t *testing.T, e *Env, params map[string]any) ([]int64, bool) {
	t.Helper()
	got, err := e.taskMemoList(nil, nums(params))
	if err != nil {
		t.Fatal(err)
	}
	r := got.(map[string]any)
	var ids []int64
	for _, m := range r["memos"].([]any) {
		ids = append(ids, m.(map[string]any)["id"].(int64))
	}
	return ids, r["more"].(bool)
}

func TestTaskMemoListPagesBackFromTheNewest(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'a', 'bob', 'human', 1), (2, 'b', 'bob', 'human', 1)")
	for i := 1; i <= 5; i++ {
		run(t, e, "INSERT INTO task_memos(id, task_id, author, body, created_at) VALUES(?, 1, 'amy', 'm', ?)", i, i)
	}
	run(t, e, "INSERT INTO task_memos(id, task_id, author, body, created_at) VALUES(9, 2, 'amy', 'other', 9)")
	for _, c := range []struct {
		params map[string]any
		ids    []int64
		more   bool
	}{
		{map[string]any{"task": 1}, []int64{1, 2, 3, 4, 5}, false},
		{map[string]any{"task": 1, "limit": 2}, []int64{4, 5}, true},
		{map[string]any{"task": 1, "limit": 2, "before": 4}, []int64{2, 3}, true},
		{map[string]any{"task": 1, "limit": 2, "before": 2}, []int64{1}, false},
		{map[string]any{"task": 1, "limit": 5}, []int64{1, 2, 3, 4, 5}, false},
	} {
		ids, more := memoIDs(t, e, c.params)
		if fmt.Sprint(ids) != fmt.Sprint(c.ids) || more != c.more {
			t.Errorf("%v: got %v more=%v, want %v more=%v", c.params, ids, more, c.ids, c.more)
		}
	}
	for _, p := range []map[string]any{{"task": 1, "before": 3}, {"task": 1, "limit": 0}, {"task": 7}} {
		if _, err := e.taskMemoList(nil, nums(p)); err == nil {
			t.Errorf("%v: no error", p)
		}
	}
}

func nums(p map[string]any) map[string]any {
	out := map[string]any{}
	for k, v := range p {
		out[k] = json.Number(fmt.Sprint(v))
	}
	return out
}

func TestAFounderCommentShowsWhereItsTellStandsForEachReader(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'a', 'bob', 'human', 1)")
	run(t, e, "INSERT INTO task_memos(id, task_id, author, body, created_at) VALUES(1, 1, 'human', 'hi', 1), (2, 1, 'amy', 'mine', 2)")
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, notice, landed_at, delivered_at, read_at) VALUES"+
		"(1, 1, 'n', 'a', 'b', 'landed', 'say', 2, 100, NULL, NULL),"+ // held for a digest
		"(2, 1, 'n', 'b', 'b', 'landed', 'say', 2, 100, 200, NULL),"+ // sent in a digest
		"(3, 1, 'n', 'c', 'b', 'landed', 'say', 2, 100, 200, 300),"+ // opened
		"(4, 1, 'n', 'd', 'b', 'landed', 'say', 0, 400, NULL, NULL)") // typed at once
	run(t, e, "INSERT INTO memo_tells(memo_id, reader, request_id) VALUES(1, 'a', 1), (1, 'b', 2), (1, 'c', 3), (1, 'd', 4), (2, 'a', 1)")
	got, err := e.taskMemoList(nil, nums(map[string]any{"task": 1}))
	if err != nil {
		t.Fatal(err)
	}
	memos := got.(map[string]any)["memos"].([]any)
	var states []string
	for _, r := range memos[0].(map[string]any)["readers"].([]any) {
		states = append(states, r.(map[string]any)["agent"].(string)+":"+r.(map[string]any)["state"].(string))
	}
	if fmt.Sprint(states) != "[a:held b:sent c:read d:read]" {
		t.Fatalf("%v", states)
	}
	if m, ok := memos[0].(map[string]any)["readers"].([]any)[0].(map[string]any)["minutes"].(int64); !ok || m < 1 {
		t.Fatalf("a held reader says how long until its digest: %v", memos[0])
	}
	if _, ok := memos[1].(map[string]any)["readers"]; ok {
		t.Fatal("only the founder's comments show readers")
	}
}
