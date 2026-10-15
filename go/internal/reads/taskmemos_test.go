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
