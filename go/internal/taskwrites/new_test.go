package taskwrites

import (
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/taskresult"
)

func TestNewOpensATaskAndAsksItsOwnerInOneTransaction(t *testing.T) {
	db, mk := discardEnv(t)
	exec(t, db,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'parent', 'manager', 'gp', 1), (2, 'blocker', 'other', 'manager', 1), "+
			"(3, 'gone', 'other', 'manager', 1)",
		"UPDATE tasks SET discarded_by = 'manager', discarded_at = 2 WHERE id = 3")
	line := func(id int, params, rid string) string {
		return fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":"task.new","params":{%s"request_id":%q}}`, id, params, rid)
	}
	code := func(r map[string]any) any { return r["error"].(map[string]any)["data"].(map[string]any)["code"] }
	base := `"owner":"worker","title":"build  it","body":"the work","time_box":"2h",`
	f := &fakeTeller{}
	r := call(t, mk("manager", f),
		line(2, base+`"priority":"p1","expects":"sha","parent":1,"blocked_by":2,`, "n1"),
		line(3, base+`"priority":"p1","expects":"sha","parent":1,"blocked_by":2,`, "n1"),
		line(4, `"owner":"worker","title":"t","time_box":"2h",`, "n2"),
		line(5, `"owner":"worker","title":"t","body":"b",`, "n3"),
		line(6, `"owner":"worker","title":"t","body":"b","time_box":"9h",`, "n4"),
		line(7, `"owner":"nobody","title":"t","body":"b","time_box":"1h",`, "n5"),
		line(8, base+`"blocked_by":3,`, "n6"),
		line(9, base+`"parent":99,`, "n7"),
		line(10, base+`"relation":"x",`, "n8"))
	res := r[0]["result"].(map[string]any)
	if res["task"].(float64) != 4 || res["said"] != "task T4 opened for worker (P1, expects sha, timeout 2h00m): queued as #1 for worker" {
		t.Fatalf("%v", r[0])
	}
	if r[1]["result"].(map[string]any)["replayed"] != true || count(t, db, "SELECT COUNT(*) FROM tasks WHERE opened_by = 'manager' AND title LIKE 'build%'") != 1 {
		t.Fatalf("a retry replays and opens nothing twice: %v", r[1])
	}
	wants := []string{"refused", "bad_request", "bad_request", "not_found", "refused", "not_found", "bad_request"}
	for i, w := range wants {
		if got := code(r[2+i]); got != w {
			t.Errorf("call %d: %v, want %s: %v", 4+i, got, w, r[2+i])
		}
	}
	for q, want := range map[string]int{
		"SELECT COUNT(*) FROM tasks": 4,
		"SELECT COUNT(*) FROM tasks WHERE id = 4 AND title = 'build  it' AND owner = 'worker' AND opened_by = 'manager' AND parent_task = 1 AND priority = 1 AND expects = 'sha' AND timeout_s = 7200 AND timeout_at IS NULL": 1,
		"SELECT COUNT(*) FROM task_requests WHERE task_id = 4":                                                      1,
		"SELECT COUNT(*) FROM agent_state WHERE agent = 'worker' AND current_task = 4":                              1,
		"SELECT COUNT(*) FROM task_blocks WHERE (task_id = 1 AND blocks_on = 4) OR (task_id = 4 AND blocks_on = 2)": 2,
		"SELECT COUNT(*) FROM events WHERE kind = 'task_new' AND json_extract(payload, '$.owner') = 'worker'":       1,
	} {
		if got := count(t, db, q); got != want {
			t.Errorf("%s: %d, want %d", q, got, want)
		}
	}

	// The founder opens a task for an agent, queued as it opens, or a todo of their own, which asks no one.
	f = &fakeTeller{}
	r = call(t, mk("", f), line(2, base, "f1"), line(3, `"owner":"human","title":"mine","body":"b","time_box":"1h",`, "f2"))
	if r[0]["error"] != nil || count(t, db, "SELECT COUNT(*) FROM tasks WHERE id = 5 AND opened_by = 'human' AND queued_by = 'human'") != 1 {
		t.Fatalf("the founder's task opens queued: %v", r[0])
	}
	if r[1]["error"] != nil || count(t, db, "SELECT COUNT(*) FROM requests WHERE requester = 'human' AND target = 'human'") != 0 ||
		count(t, db, "SELECT COUNT(*) FROM tasks WHERE id = 6 AND owner = 'human' AND opened_by = 'human' AND queued_by = 'human' AND expects = 'report'") != 1 ||
		count(t, db, "SELECT COUNT(*) FROM task_requests WHERE task_id = 6") != 0 ||
		count(t, db, "SELECT COUNT(*) FROM events WHERE kind = 'task_new' AND json_extract(payload, '$.task_id') = 6 AND json_extract(payload, '$.request_id') IS NULL") != 1 {
		t.Fatalf("the founder's own todo opens queued, a report, with no request: %v", r[1])
	}
	f = &fakeTeller{deny: "no edge"}
	r = call(t, mk("manager", f), line(2, base, "d1"))
	if code(r[0]) != "refused" || count(t, db, "SELECT COUNT(*) FROM tasks") != 6 || count(t, db, "SELECT COUNT(*) FROM requests WHERE state = 'denied'") != 1 {
		t.Fatalf("a refused owner leaves no task behind, only the denial: %v", r[0])
	}
}

func TestNewReturnsBeforeTheOwnerIsTold(t *testing.T) {
	db, mk := discardEnv(t)
	release, told := make(chan struct{}), make(chan struct{})
	f := &fakeTeller{after: func() string {
		<-release // a closed owner's session starts here and the telling waits on it
		close(told)
		return "delivered"
	}}
	r := call(t, mk("manager", f), `{"jsonrpc":"2.0","id":1,"method":"task.new","params":{"owner":"worker","title":"t","body":"b","time_box":"1h","request_id":"a1"}}`)
	if r[0]["error"] != nil || count(t, db, "SELECT COUNT(*) FROM tasks WHERE title = 't' AND owner = 'worker'") != 1 {
		t.Fatalf("the task is open when the call returns: %v", r[0])
	}
	if said := r[0]["result"].(map[string]any)["said"].(string); strings.Contains(said, "founder's screen") {
		t.Fatalf("an agent's task does not say it is on the founder's screen: %q", said)
	}
	r = call(t, mk("manager", &fakeTeller{}), `{"jsonrpc":"2.0","id":2,"method":"task.new","params":{"owner":"human","title":"d","body":"b","time_box":"1h","request_id":"a2"}}`)
	if said, _ := r[0]["result"].(map[string]any)["said"].(string); !strings.Contains(said, "it is on the founder's screen (`hl`)") {
		t.Fatalf("a founder's task says where it is: %v", r[0])
	}
	select {
	case <-told:
		t.Fatal("the call returned only after the owner was told")
	default:
	}
	close(release)
	select {
	case <-told:
	case <-time.After(5 * time.Second):
		t.Fatal("the owner is told after the call has returned")
	}
}

func TestNewMarksAUITaskAndRefusesAUIThatIsNotTrueOrFalse(t *testing.T) {
	db, mk := discardEnv(t)
	line := func(id int, params, rid string) string {
		return fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":"task.new","params":{"owner":"worker","title":"t","body":"b","time_box":"1h",%s"request_id":%q}}`, id, params, rid)
	}
	r := call(t, mk("manager", &fakeTeller{}), line(2, `"ui":true,`, "ui1"), line(3, `"ui":"yes",`, "ui2"), line(4, ``, "ui3"))
	if r[0]["error"] != nil || r[2]["error"] != nil || r[1]["error"].(map[string]any)["data"].(map[string]any)["field"] != "ui" {
		t.Fatalf("%v", r)
	}
	for id, want := range map[int64]bool{int64(r[0]["result"].(map[string]any)["task"].(float64)): true, int64(r[2]["result"].(map[string]any)["task"].(float64)): false} {
		if got, err := taskresult.IsUI(db, id); err != nil || got != want {
			t.Errorf("T%d ui %v, want %v (%v)", id, got, want, err)
		}
	}
}
