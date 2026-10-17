package taskwrites

import (
	"fmt"
	"strings"
	"testing"
)

func TestTagsAreSetOnNewAndChangedByEditEachChangeKept(t *testing.T) {
	db, mk := discardEnv(t)
	line := func(id int, method, params, rid string) string {
		return fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":%q,"params":{%s"request_id":%q}}`, id, method, params, rid)
	}
	code := func(r map[string]any) any { return r["error"].(map[string]any)["data"].(map[string]any)["code"] }
	var tooMany []string
	for i := 0; i < maxTags; i++ { // with the two T1 keeps, one past the most
		tooMany = append(tooMany, fmt.Sprintf(`"x%d"`, i))
	}
	many := `"add_tags":[` + strings.Join(tooMany, ",")
	r := call(t, mk("manager", &fakeTeller{}),
		line(2, "task.new", `"owner":"worker","title":"t","tags":["FE"," be ","fe"],`, "n1"),
		line(3, "task.new", `"owner":"worker","title":"u","tags":["two words"],`, "n2"),
		line(4, "task.edit", `"task":1,"add_tags":["ops","be"],"remove_tags":["fe"],`, "e1"),
		line(5, "task.edit", `"task":1,"add_tags":["ops"],"remove_tags":["fe"],`, "e2"),
		line(6, "task.edit", `"task":1,`+many+`],`, "e3"),
		line(7, "task.edit", `"task":9,"add_tags":["ops"],`, "e4"),
		line(8, "task.edit", `"task":1,"add_tags":"ops",`, "e5"))
	if r[0]["result"] == nil || code(r[1]) != "bad_request" {
		t.Fatalf("%v", r[:2])
	}
	if got := r[2]["result"].(map[string]any)["said"]; got != "task T1 tags -fe +ops" {
		t.Fatalf("%v", got)
	}
	if code(r[3]) != "refused" || code(r[4]) != "refused" || code(r[5]) != "not_found" || code(r[6]) != "bad_request" {
		t.Fatalf("nothing to change, too many, no such task, not a list: %v", r[3:])
	}
	for q, want := range map[string]int{
		"SELECT COUNT(*) FROM task_tags WHERE task_id = 1":                                                                                2,
		"SELECT COUNT(*) FROM task_tags WHERE task_id = 1 AND tag IN ('be', 'ops')":                                                       2,
		"SELECT COUNT(*) FROM tasks":                                                                                                      1,
		`SELECT COUNT(*) FROM events WHERE kind = 'task_new' AND payload LIKE '%"tags": ["fe", "be"]%'`:                                   1,
		`SELECT COUNT(*) FROM events WHERE kind = 'edit_task' AND payload = '{"task_id": 1, "tags": {"add": ["ops"], "remove": ["fe"]}}'`: 1,
		"SELECT COUNT(*) FROM events WHERE kind = 'edit_task'":                                                                            1,
	} {
		if got := count(t, db, q); got != want {
			t.Errorf("%s: %d, want %d", q, got, want)
		}
	}
}

func TestASavedViewIsKeptReplacedAndDeletedByName(t *testing.T) {
	db, mk := discardEnv(t)
	line := func(id int, method, params, rid string) string {
		return fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":%q,"params":{%s"request_id":%q}}`, id, method, params, rid)
	}
	code := func(r map[string]any) any { return r["error"].(map[string]any)["data"].(map[string]any)["code"] }
	r := call(t, mk(""),
		line(2, "task.view.save", `"name":" fe work ","filter":{"tags":["FE"],"state":"doing"},`, "v1"),
		line(3, "task.view.save", `"name":"fe work","filter":{"tags":["fe"],"owner":"worker"},`, "v2"),
		line(4, "task.view.save", `"name":"x","filter":{"title":"y"},`, "v3"),
		line(5, "task.view.save", `"name":"x","filter":{"state":"busy"},`, "v4"),
		line(6, "task.view.save", `"name":"","filter":{},`, "v5"),
		line(7, "task.view.delete", `"name":"nope",`, "v6"),
		line(8, "task.view.save", `"name":"gone","filter":{},`, "v7"),
		line(9, "task.view.delete", `"name":"gone",`, "v8"))
	if r[0]["result"] == nil || r[1]["result"] == nil || r[6]["result"] == nil || r[7]["result"] == nil {
		t.Fatalf("%v", r)
	}
	for i, want := range []string{"bad_request", "bad_request", "bad_request", "not_found"} {
		if got := code(r[2+i]); got != want {
			t.Errorf("call %d: %v, want %s", 4+i, got, want)
		}
	}
	if got := count(t, db, `SELECT COUNT(*) FROM task_views WHERE name = 'fe work' AND filter = '{"owner":"worker","tags":["fe"]}' AND updated_by = 'human'`); got != 1 {
		t.Fatalf("the second save replaces the first under the trimmed name: %d", got)
	}
	if got := count(t, db, "SELECT COUNT(*) FROM task_views"); got != 1 {
		t.Fatalf("views left: %d", got)
	}
	if r := call(t, mk("worker"), line(2, "task.view.save", `"name":"mine","filter":{},`, "a1")); r[0]["error"] == nil {
		t.Fatalf("an agent saves no view: %v", r)
	}
}
