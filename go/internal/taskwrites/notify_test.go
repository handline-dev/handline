package taskwrites

import (
	"fmt"
	"sort"
	"strings"
	"testing"
)

func TestAnyoneChangesATaskAndItsOpenerAndAssigneeAreTold(t *testing.T) {
	db, mk := discardEnv(t)
	exec(t, db,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'one', 'worker', 'manager', 1), (2, 'two', 'worker', 'manager', 1), "+
			"(3, 'three', 'worker', 'manager', 1), (4, 'four', 'other', 'manager', 1), (5, 'five', 'worker', 'manager', 1), (6, 'six', 'worker', 'manager', 1)",
		"UPDATE tasks SET done_evidence = 'sha', done_at = 2 WHERE id = 3")
	f := &fakeTeller{}
	rpcLine := func(id int, method, params string) string {
		return fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":%q,"params":{%s,"request_id":"r%d"}}`, id, method, params, id)
	}
	r := call(t, mk("gp", f),
		rpcLine(2, "task.edit", `"task":1,"title":"renamed"`),
		rpcLine(3, "task.discard", `"task":1,"reason":"not needed"`),
		rpcLine(4, "task.queue", `"tasks":[2]`),
		rpcLine(5, "task.reopen", `"task":3,"note":"it was wrong"`),
		rpcLine(6, "task.merge", `"into":4,"tasks":[5]`),
		rpcLine(7, "task.withdraw", `"task":6`))
	for i := 0; i < 5; i++ {
		if r[i]["result"] == nil {
			t.Fatalf("%d: a stranger may: %v", i, r[i])
		}
	}
	if !strings.Contains(errText(r[5]), "only they withdraw it") {
		t.Fatalf("a withdraw has no reason, so it stays the asker's: %v", r[5])
	}
	toldOf := func(text string) string {
		var to []string
		rows, err := db.Query("SELECT target FROM requests WHERE requester = 'task-board' AND body = ?", text)
		if err != nil {
			t.Fatal(err)
		}
		defer rows.Close()
		for rows.Next() {
			var s string
			rows.Scan(&s)
			to = append(to, s)
		}
		sort.Strings(to)
		return strings.Join(to, " ")
	}
	for text, want := range map[string]string{
		"task T1 edited by gp: title 'one' -> 'renamed'": "manager worker",
		"T1 discarded by gp: not needed":                 "manager worker",
		"task T2 is queued (gp)":                         "manager worker",
		"task T3 reopened by gp: it was wrong":           "manager worker",
		"T5 merged into T4 by gp":                        "manager other worker",
	} {
		if got := toldOf(text); got != want {
			t.Errorf("%q told %q, want %q", text, got, want)
		}
	}
	if len(f.notified) != 9 || len(f.told) != 2 {
		t.Fatalf("the notices go to the Teller after the commit: %d notified, %d told by the discard", len(f.notified), len(f.told))
	}
	for kind, n := range map[string]int{"edit_task": 1, "discard_task": 1, "queue_task": 1, "reopen_task": 1, "merge_task": 1} {
		if got := count(t, db, fmt.Sprintf("SELECT COUNT(*) FROM events WHERE kind = '%s' AND agent = 'gp'", kind)); got != n {
			t.Errorf("%s by gp: %d, want %d: each change is kept with who made it", kind, got, n)
		}
	}
	call(t, mk("manager", f), rpcLine(8, "task.edit", `"task":6,"title":"six again"`))
	if got := toldOf("task T6 edited by manager: title 'six' -> 'six again'"); got != "worker" {
		t.Fatalf("the actor is not told of its own change: %q", got)
	}
}

func TestADiscardTellsTheAskerBesideTheOpenerAndAMoveLeavesTheNewOwnerToItsBrief(t *testing.T) {
	db, mk := discardEnv(t)
	exec(t, db, "INSERT INTO tasks(id, title, owner, opened_by, created_at, reporter) VALUES(1, 'one', 'worker', 'manager', 1, 'other'), "+
		"(2, 'two', 'worker', 'manager', 1, NULL)")
	f := &fakeTeller{}
	r := call(t, mk("gp", f),
		`{"jsonrpc":"2.0","id":2,"method":"task.discard","params":{"task":1,"reason":"dup","request_id":"d1"}}`,
		`{"jsonrpc":"2.0","id":3,"method":"task.edit","params":{"task":2,"owner":"other","request_id":"e1"}}`)
	if r[0]["result"] == nil || r[1]["result"] == nil {
		t.Fatalf("%v %v", r[0], r[1])
	}
	targets := func(like string) (to string) {
		db.QueryRow("SELECT group_concat(target, ' ') FROM (SELECT target FROM requests WHERE body LIKE ? ORDER BY target)", like).Scan(&to)
		return
	}
	if got := targets("T1 discarded by gp: dup%"); got != "manager other worker" {
		t.Errorf("a discard tells the asker, the opener and the assignee: %q", got)
	}
	if got := targets("task T2 edited by gp%"); got != "manager worker" {
		t.Errorf("a move tells the opener and the old owner; the new one is sent the brief itself: %q", got)
	}
}
