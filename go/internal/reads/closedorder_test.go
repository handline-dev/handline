package reads

import (
	"encoding/json"
	"fmt"
	"testing"

	"github.com/handline/handline/go/internal/rpc"
)

var asFounderConn = &rpc.Conn{Caller: rpc.Caller{Role: "founder"}}

// closedPage is one page of tasks.list in closed order: its ids and the cursor to go on with ("" at the end).
func closedPage(t *testing.T, e *Env, params map[string]any) (ids string, next string) {
	t.Helper()
	got, err := e.tasksList(asFounderConn, params)
	if err != nil {
		t.Fatal(err)
	}
	res := got.(map[string]any)
	var list []any
	for _, it := range res["items"].([]any) {
		list = append(list, it.(map[string]any)["id"])
	}
	next, _ = res["next"].(string)
	if (next != "") != (res["more"] == true) {
		t.Errorf("more %v with next %q", res["more"], next)
	}
	return fmt.Sprint(list), next
}

func TestClosedOrderListsTheNewestCloseFirstWhateverTheIdAndPagesWithoutGapOrRepeat(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, done_evidence, done_at) VALUES"+
		"(1, 'old id closed last', 'bob', 'amy', 1, 'x', 100), (2, 'b', 'bob', 'amy', 1, 'x', 50), (4, 'd', 'bob', 'amy', 1, 'x', 100), "+
		"(5, 'e', 'bob', 'amy', 1, 'x', 30), (7, 'open', 'bob', 'amy', 1, NULL, NULL)")
	got, _ := closedPage(t, e, map[string]any{"state": "done", "order": "closed"})
	if got != "[4 1 2 5]" {
		t.Fatalf("closed order: %s (a tie goes to the newer id)", got)
	}
	if byID, _ := closedPage(t, e, map[string]any{"state": "done"}); byID != "[5 4 2 1]" {
		t.Fatalf("id order, unchanged: %s", byID)
	}
	var all string
	for cursor, n := "", 0; ; n++ {
		params := map[string]any{"state": "done", "order": "closed", "limit": json.Number("1")}
		if cursor != "" {
			params["cursor"] = cursor
		}
		ids, next := closedPage(t, e, params)
		all += ids
		if cursor = next; cursor == "" {
			break
		}
		if n > 5 {
			t.Fatal("paging does not end")
		}
	}
	if all != "[4][1][2][5]" {
		t.Errorf("one row a page: %s", all)
	}
}

func TestClosedOrderReadsADiscardedTasksDiscardTimeNotItsDoneTime(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, discarded_by, discarded_at, done_at) VALUES"+
		"(3, 'c', 'bob', 'amy', 1, 'amy', 100, NULL), (6, 'f', 'bob', 'amy', 1, 'amy', 10, 999)")
	if got, _ := closedPage(t, e, map[string]any{"state": "discarded", "order": "closed"}); got != "[3 6]" {
		t.Fatalf("%s", got)
	}
}

func TestClosedOrderRefusesWhatItCannotPage(t *testing.T) {
	e := testEnv(t)
	for name, params := range map[string]map[string]any{
		"no state":            {"order": "closed"},
		"an open state":       {"order": "closed", "state": "doing"},
		"with before":         {"order": "closed", "state": "done", "before": json.Number("5")},
		"an unknown order":    {"order": "oldest", "state": "done"},
		"a cursor, id order":  {"state": "done", "cursor": closedCursor(5, 2)},
		"a garbled cursor":    {"order": "closed", "state": "done", "cursor": "!!"},
		"a cursor of no task": {"order": "closed", "state": "done", "cursor": closedCursor(5, 0)},
	} {
		if _, err := e.tasksList(asFounderConn, params); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
}
