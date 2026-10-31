package reads

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

func TestRequestsAreReadFromTheirTreeOfTasks(t *testing.T) {
	e := testEnv(t)
	now := time.Now().Unix()
	at := func(n int64) string { return itoa(now - n) }
	run(t, e, "INSERT INTO requests(id, ref, ts, requester, target, body, state, kind, edge_ok) VALUES"+
		"(1, 'aaaaaaaaaaa1', "+at(900)+", 'human', 'lead', 'make it fast', 'landed', 'say', 1), "+
		"(2, 'aaaaaaaaaaa2', "+at(800)+", 'human', 'lead', 'thanks', 'landed', 'say', 1), "+
		"(3, 'aaaaaaaaaaa3', "+at(700)+", 'human', 'lead', 'still nothing', 'landed', 'say', 1), "+
		"(4, 'aaaaaaaaaaa4', "+at(600)+", 'human', 'lead', 'ship it', 'landed', 'say', 1), "+
		"(5, 'aaaaaaaaaaa5', "+at(500)+", 'human', 'lead', 'ask me', 'landed', 'say', 1)")
	run(t, e, "UPDATE requests SET no_work = 'just thanks' WHERE id = 2")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, parent_task, created_at, from_request, pr, done_evidence, done_at) VALUES"+
		"(10, 'fast', 'lead', 'human', NULL, "+at(850)+", 1, NULL, NULL, NULL), "+
		"(11, 'child a', 'dev', 'lead', 10, "+at(840)+", NULL, 55, NULL, NULL), "+
		"(12, 'child b', 'dev2', 'lead', 10, "+at(830)+", NULL, NULL, 'merged abc', "+at(100)+"), "+
		"(20, 'shipped', 'lead', 'human', NULL, "+at(550)+", 4, NULL, 'done', "+at(200)+"), "+
		"(30, 'asking', 'lead', 'human', NULL, "+at(450)+", 5, NULL, NULL, NULL), "+
		"(31, 'decide', 'human', 'lead', 30, "+at(440)+", NULL, NULL, NULL, NULL), "+
		"(40, 'a todo', 'human', 'human', NULL, "+at(400)+", NULL, NULL, NULL, NULL)")
	run(t, e, "INSERT INTO task_requests(task_id, request_id) VALUES(10, 1), (20, 4), (30, 5)")
	rows, err := e.RequestRows(now)
	if err != nil {
		t.Fatal(err)
	}
	by := map[string]map[string]any{}
	for _, r := range rows {
		if r["task"] != nil {
			by["T"+itoa(r["task"].(int64))] = r
		} else {
			by[r["ref"].(string)] = r
		}
	}
	want := map[string]string{"T10": "review", "T20": "done", "T30": "waiting_on_you", "msg_aaaaaaaaaaa2": "no_work", "msg_aaaaaaaaaaa3": "not_yet"}
	for k, st := range want {
		if by[k] == nil || by[k]["status"] != st {
			t.Errorf("%s: %v, want %s", k, by[k], st)
		}
	}
	if len(rows) != len(want) || by["T40"] != nil {
		t.Errorf("a todo of the founder's own is not a request, and nothing is listed twice: %d rows", len(rows))
	}
	if r := by["T10"]; r["open"] != 1 || r["done"] != 1 || r["agent"] != "lead" || r["ref"] != "msg_aaaaaaaaaaa1" ||
		len(r["holders"].([]string)) != 2 || len(r["prs"].([]int64)) != 1 {
		t.Errorf("a tree is counted: %v", r)
	}
	for k, want := range map[string]string{"T10": "sub 1/2 done, now dev, in review", "T20": "done", "T30": "sub 0/1 done, now lead, waiting on you", "msg_aaaaaaaaaaa3": ""} { // local-ref-ok
		if by[k]["progress"] != want {
			t.Errorf("%s progress %q, want %q", k, by[k]["progress"], want)
		}
	}
	if rows[0]["task"] != int64(30) && rows[0]["cursor"].(string) < rows[len(rows)-1]["cursor"].(string) {
		t.Errorf("newest first: %v", rows)
	}
}

func TestRequestsListFiltersByStatusAndPages(t *testing.T) {
	e := testEnv(t)
	now := time.Now().Unix()
	run(t, e, "INSERT INTO requests(id, ref, ts, requester, target, body, state, kind, edge_ok) VALUES"+
		"(1, 'aaaaaaaaaaa1', "+itoa(now-30)+", 'human', 'lead', 'one', 'landed', 'say', 1), (2, 'aaaaaaaaaaa2', "+itoa(now-20)+", 'human', 'lead', 'two', 'landed', 'say', 1)")
	out, err := e.requestsList(nil, map[string]any{"state": "not_yet", "limit": json.Number("1")})
	if err != nil {
		t.Fatal(err)
	}
	page := out.(map[string]any)
	items := page["items"].([]any)
	if len(items) != 1 || page["more"] != true || items[0].(map[string]any)["title"] != "two" {
		t.Fatalf("%v", page)
	}
	next, _ := e.requestsList(nil, map[string]any{"state": "not_yet", "limit": json.Number("1"), "before": items[0].(map[string]any)["cursor"]})
	if got := next.(map[string]any)["items"].([]any); len(got) != 1 || got[0].(map[string]any)["title"] != "one" || next.(map[string]any)["more"] != false {
		t.Fatalf("%v", next)
	}
	if _, err := e.requestsList(nil, map[string]any{"state": "nope"}); err == nil {
		t.Fatal("an unknown state is refused")
	}
}

func TestAFoundersChatMessageSaysWhatBecameOfIt(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO requests(id, ref, ts, requester, target, body, state, kind, edge_ok, landed_at, no_work) VALUES"+
		"(1, 'aaaaaaaaaaa1', 10, 'human', 'lead', 'make it', 'landed', 'say', 1, 10, NULL), (2, 'aaaaaaaaaaa2', 20, 'human', 'lead', 'thanks', 'landed', 'say', 1, 20, 'just thanks'), "+
		"(3, 'aaaaaaaaaaa3', 30, 'lead', 'human', 'done', 'landed', 'say', 1, 30, NULL)")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, from_request) VALUES(5, 'made', 'lead', 'human', 1, 1)")
	pass := e.ChatRequests()
	for id, want := range map[int64][2]any{1: {int64(5), nil}, 2: {nil, "just thanks"}} {
		req, err := pass.Get(id)
		if err != nil || req == nil {
			t.Fatalf("%d: %v", id, err)
		}
		m := req.Message
		if m["request_task"] != want[0] || m["no_work"] != want[1] {
			t.Errorf("%d: %v %v", id, m["request_task"], m["no_work"])
		}
	}
	req, _ := pass.Get(3)
	if _, has := req.Message["request_task"]; has {
		t.Errorf("an agent's message carries neither field")
	}
}

func TestThePlainMessagesReadScansNoTable(t *testing.T) {
	e := testEnv(t)
	rows, err := e.DB.Query("EXPLAIN QUERY PLAN "+plainMessagesSQL, human, human, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	for rows.Next() {
		var id, parent, unused int
		var detail string
		if err := rows.Scan(&id, &parent, &unused, &detail); err != nil {
			t.Fatal(err)
		}
		if strings.HasPrefix(detail, "SCAN ") {
			t.Errorf("the read scans a whole table per message: %s", detail)
		}
	}
}
