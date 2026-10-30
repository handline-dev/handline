package reads

import (
	"encoding/json"
	"testing"

	"github.com/handline/handline/go/internal/rpc"
)

func TestInboxListFiltersAndPagesBeforeBuildingItems(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	addAgent(t, e, "cat", "")
	run(t, e, "INSERT INTO requests(id, ref, ts, requester, target, body, state, kind, edge_ok) VALUES"+
		"(1, 'aaaaaaaaaaa1', 10, 'bob', 'human', 'one', 'queued', 'ask', 1), "+
		"(2, 'aaaaaaaaaaa2', 20, 'cat', 'human', 'two', 'queued', 'ask', 1), "+
		"(3, 'aaaaaaaaaaa3', 30, 'bob', 'human', 'three', 'queued', 'task', 1), "+
		"(4, 'aaaaaaaaaaa4', 40, 'bob', 'human', 'four', 'queued', 'ask', 1)")
	run(t, e, `INSERT INTO events(ts, agent, kind, payload) VALUES(30, 'bob', 'raise_decision', '{"request_id": 3, "task_id": 1, "reason": "heal", "fix": "open_terminal"}')`)
	founder := &rpc.Conn{Caller: rpc.Caller{Role: "founder", Info: map[string]any{"kind": "person"}}}

	ids := func(params map[string]any) ([]int64, bool) {
		t.Helper()
		page, err := e.inboxPage(founder, params)
		if err != nil {
			t.Fatal(err)
		}
		var out []int64
		for _, it := range page["items"].([]any) {
			out = append(out, it.(map[string]any)["id"].(int64))
		}
		return out, page["more"].(bool)
	}
	same := func(got []int64, want ...int64) bool {
		if len(got) != len(want) {
			return false
		}
		for i := range got {
			if got[i] != want[i] {
				return false
			}
		}
		return true
	}
	if got, more := ids(map[string]any{"limit": json.Number("2")}); !same(got, 4, 3) || !more {
		t.Fatalf("newest two, more left: %v %v", got, more)
	}
	if got, more := ids(map[string]any{"limit": json.Number("2"), "before": json.Number("3")}); !same(got, 2, 1) || more {
		t.Fatalf("the page before 3: %v %v", got, more)
	}
	if got, _ := ids(map[string]any{"agent": "bob", "kind": "ask"}); !same(got, 4, 1) {
		t.Fatalf("bob's asks: %v", got)
	}
	page, err := e.inboxPage(founder, map[string]any{"limit": json.Number("1"), "before": json.Number("4")})
	if err != nil {
		t.Fatal(err)
	}
	if it := page["items"].([]any)[0].(map[string]any); it["fix"] != "open_terminal" || it["title"] != "three" {
		t.Fatalf("a paged row is built whole: %v", it)
	}
}
