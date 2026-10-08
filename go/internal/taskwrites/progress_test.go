package taskwrites

import (
	"fmt"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
)

func TestProgressIsKeptOnTheTaskTheAgentHoldsOrTheAgentAndReadBack(t *testing.T) {
	db, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	for _, q := range []string{
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 't', 'worker', 'manager', 1)",
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(2, 'u', 'worker', 'manager', 1)",
	} {
		if _, err := db.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
	mk := func(agent string) *rpc.Server {
		s := rpc.NewServer(auth{agent}, "x", store.LatestVersion(), "local", func(string, ...any) {})
		s.DB = db
		Register(s, t.TempDir(), nil)
		return s
	}
	prog := func(id int, params, rid string) string {
		return fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":"task.progress","params":{%s"request_id":%q}}`, id, params, rid)
	}
	get := `{"jsonrpc":"2.0","id":9,"method":"progress.get","params":{}}`
	note := func(r map[string]any) any { return r["result"].(map[string]any)["note"] }
	code := func(r map[string]any) any { return r["error"].(map[string]any)["data"].(map[string]any)["code"] }

	r := call(t, mk("worker"),
		prog(2, `"note":"on the agent",`, "p1"), get,
		`{"jsonrpc":"2.0","id":4,"method":"task.start","params":{"task":1,"request_id":"s1"}}`,
		prog(5, `"note":"on one",`, "p2"), get,
		prog(7, `"task":2,"note":"on two",`, "p3"), get,
		prog(9, `"note":"",`, "p4"), get)
	if note(r[1]) != "on the agent" {
		t.Fatalf("with no task the note is the agent's: %v", r[1])
	}
	if note(r[4]) != "on one" || note(r[6]) != "on one" {
		t.Fatalf("holding T1, the note is on T1 and a note on T2 leaves it alone: %v %v", r[4], r[6])
	}
	if said := r[5]["result"].(map[string]any)["said"]; said != "progress on T2 kept; the handoff and the wake prompt carry it" {
		t.Fatalf("%v", r[5])
	}
	var kept string
	db.QueryRow("SELECT value FROM meta WHERE name = 'progress-task:2'").Scan(&kept)
	if kept != "on two" {
		t.Fatalf("T2's note: %q", kept)
	}
	if note(r[8]) != nil {
		t.Fatalf("an empty note clears it: %v", r[8])
	}

	r = call(t, mk("manager"), prog(2, `"task":1,"note":"x",`, "q1"), prog(3, `"task":9,"note":"x",`, "q2"))
	if code(r[0]) != "refused" || code(r[1]) != "not_found" {
		t.Fatalf("another agent's task is refused, an unknown one is not_found: %v %v", r[0], r[1])
	}
	r = call(t, mk("worker"), prog(2, `"note":5,`, "q3"), prog(3, `"note":"`+strings.Repeat("x", 1501)+`",`, "q4"), prog(4, `"task":"a","note":"x",`, "q5"))
	for i, got := range r {
		if code(got) != "bad_request" {
			t.Fatalf("call %d: %v", i, got)
		}
	}
}
