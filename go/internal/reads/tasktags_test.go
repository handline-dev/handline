package reads

import (
	"encoding/json"
	"fmt"
	"reflect"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/rpc"
)

var asAmy = &rpc.Conn{Caller: rpc.Caller{Role: "agent", Info: map[string]any{"kind": "agent", "name": "amy"}}}

func TestTasksListFiltersOnTagsAndTheOtherFieldsTogether(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, reporter, parent_task, done_evidence) VALUES"+
		"(1, 'a', 'bob', 'amy', 1, NULL, NULL, NULL), (2, 'b', 'bob', 'amy', 1, 'cy', NULL, NULL), (3, 'c', 'cy', 'amy', 1, NULL, NULL, 'abc'), "+
		"(4, 'd', 'bob', 'human', 1, NULL, 1, NULL), (5, 'e', 'cat', 'cat', 1, NULL, NULL, NULL)")
	run(t, e, "INSERT INTO task_tags(task_id, tag) VALUES(1, 'fe'), (1, 'be'), (2, 'fe'), (3, 'fe')")
	for _, a := range []string{"amy", "bob", "cy", "cat"} {
		addAgent(t, e, a, "")
	}
	run(t, e, `INSERT INTO agent_view(agent, view, updated) VALUES('bob', '{"parent": "amy"}', 1)`)
	ids := func(c *rpc.Conn, params map[string]any) string {
		t.Helper()
		got, err := e.tasksList(c, params)
		if err != nil {
			return err.Error()
		}
		var out []string
		for _, it := range got.(map[string]any)["items"].([]any) {
			out = append(out, fmt.Sprint(it.(map[string]any)["id"]))
		}
		return fmt.Sprint(out)
	}
	tags := func(ts ...string) []any {
		out := make([]any, len(ts))
		for i, s := range ts {
			out[i] = s
		}
		return out
	}
	for _, c := range []struct {
		params map[string]any
		want   string
	}{
		{map[string]any{"tags": tags("fe")}, "[2 1]"},
		{map[string]any{"tags": tags("FE"), "all": true}, "[3 2 1]"},
		{map[string]any{"tags": tags("fe", "be")}, "[1]"},
		{map[string]any{"tags": tags("ops")}, "[]"},
		{map[string]any{"tags": tags("fe"), "owner": "bob"}, "[2 1]"},
		{map[string]any{"reporter": "cy"}, "[2]"},
		{map[string]any{"reporter": "human"}, "[4]"},
		{map[string]any{"from": "cat"}, "[5]"},
		{map[string]any{"state": "done", "tags": tags("fe")}, "[3]"},
		{map[string]any{"under": json.Number("1")}, "[4 1]"},
		{map[string]any{"parent": json.Number("1")}, "[4]"},
		{map[string]any{"before": json.Number("2")}, "[1]"},
		{map[string]any{"limit": json.Number("1")}, "[5]"},
	} {
		if got := ids(asFounder, c.params); got != c.want {
			t.Errorf("%v: %s, want %s", c.params, got, c.want)
		}
	}
	// an agent sees the tasks it or its reports work or answer for: amy has bob under it
	if got := ids(asAmy, map[string]any{"tags": tags("fe")}); got != "[2 1]" {
		t.Errorf("amy's scope: %s", got)
	}
	if got := ids(asCat, map[string]any{}); got != "[5]" {
		t.Errorf("cat's scope: %s", got)
	}
	got, _ := e.tasksList(asFounder, map[string]any{"tags": tags("fe", "be")})
	if tg := got.(map[string]any)["items"].([]any)[0].(map[string]any)["tags"]; !reflect.DeepEqual(tg, []string{"be", "fe"}) {
		t.Errorf("a Task carries its tags in order: %v", tg)
	}
	if _, err := e.tasksList(asFounder, map[string]any{"tags": "fe"}); err == nil {
		t.Error("tags is a list")
	}
}

func TestATagAddedOrRemovedIsARowOfTheHistory(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'a', 'bob', 'amy', 1)")
	run(t, e, `INSERT INTO events(ts, agent, kind, payload) VALUES(10, 'amy', 'task_new', '{"task_id": 1, "owner": "bob", "tags": ["fe"]}'), `+
		`(20, 'bob', 'edit_task', '{"task_id": 1, "tags": {"add": ["be"], "remove": ["fe"]}}')`)
	got, err := e.taskHistory(asFounder, map[string]any{"task": json.Number("1")})
	if err != nil {
		t.Fatal(err)
	}
	var rows []string
	for _, r := range got.(map[string]any)["rows"].([]any) {
		if m := r.(map[string]any); m["field"] == "tag" {
			rows = append(rows, fmt.Sprintf("%v %q->%q", m["by"], m["was"], m["now"]))
		}
	}
	want := []string{`bob ""->"be"`, `bob "fe"->""`, `amy ""->"fe"`}
	if !reflect.DeepEqual(rows, want) {
		t.Fatalf("%v, want %v", rows, want)
	}
}

func TestSavedViewsAreListedByNameWithTheirFilters(t *testing.T) {
	e := testEnv(t)
	run(t, e, `INSERT INTO task_views(name, filter, updated_by, updated_at) VALUES('z', '{}', 'human', 5), ('fe work', '{"tags":["fe"],"state":"doing"}', 'human', 6)`)
	got, err := e.taskViews(asFounder, nil)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := json.Marshal(got)
	want := `{"views":[{"at":"1970-01-01T00:00:06Z","by":"human","filter":{"state":"doing","tags":["fe"]},"name":"fe work"},` +
		`{"at":"1970-01-01T00:00:05Z","by":"human","filter":{},"name":"z"}]}`
	if string(b) != want {
		t.Fatalf("%s", b)
	}
}

func TestWhoATaskAnswersToIsReadFromItsOwnRequestsNotTheFoundersAll(t *testing.T) {
	e := testEnv(t)
	plan, err := query(e.DB, "EXPLAIN QUERY PLAN SELECT id FROM tasks WHERE "+askerSQL+" = 'x'")
	if err != nil {
		t.Fatal(err)
	}
	for _, r := range plan {
		if strings.Contains(r.str("detail"), "requests_requester") {
			t.Fatalf("the asker reads every request of the founder's for each task: %v", plan)
		}
	}
}
