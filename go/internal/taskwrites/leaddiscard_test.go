package taskwrites

import (
	"database/sql"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
)

// leadEnv is a team led by lead (w1, w2 under it) and another led by ops: T1 and T2 are w1's, opened by lead, each with a request still
// queued from w2; T3 is ops's own.
func leadEnv(t *testing.T) (*sql.DB, func(agent string, tell Teller) *rpc.Server) {
	t.Helper()
	d, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	home := t.TempDir()
	for _, a := range []string{"lead", "w1", "w2", "ops"} {
		os.MkdirAll(filepath.Join(home, "agents", a), 0o755)
		os.WriteFile(filepath.Join(home, "agents", a, "agent.yaml"), nil, 0o644)
	}
	os.WriteFile(filepath.Join(home, "team.yaml"), []byte("teams:\n  lead: [lead, w1, w2]\n  ops: [ops]\nagents:\n  lead: {}\n  w1: {}\n  w2: {}\n  ops: {}\n"), 0o644)
	exec(t, d,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'first', 'w1', 'lead', 1), (2, 'second', 'w1', 'lead', 1), (3, 'theirs', 'ops', 'ops', 1)",
		"INSERT INTO requests(id, ts, requester, target, body, kind, state, edge_ok) VALUES(5, 1, 'w2', 'w1', 'look at t', 'ask', 'queued', 1), "+
			"(6, 1, 'w2', 'w1', 'and u', 'ask', 'queued', 1)",
		"INSERT INTO task_requests(task_id, request_id) VALUES(1, 5), (2, 6)")
	return d, func(agent string, tell Teller) *rpc.Server {
		s := rpc.NewServer(auth{agent}, "x", store.LatestVersion(), "local", func(string, ...any) {})
		s.DB = d
		Register(s, home, tell, nil)
		return s
	}
}

func TestALeadDiscardsATaskOfItsTeamWithdrawingItsOpenRequest(t *testing.T) {
	d, mk := leadEnv(t)
	f := &fakeTeller{}
	r := call(t, mk("lead", f), `{"jsonrpc":"2.0","id":2,"method":"task.discard","params":{"task":1,"reason":"PR merged","request_id":"l1"}}`)
	if r[0]["error"] != nil {
		t.Fatal(r[0])
	}
	if count(t, d, "SELECT COUNT(*) FROM requests WHERE id = 5 AND state != 'queued'") != 1 {
		t.Fatal("the open request is withdrawn")
	}
	if len(f.cancelled) != 1 || f.cancelled[0].RID != 5 || f.cancelled[0].Verdict != "withdrawn by lead: PR merged" {
		t.Fatalf("its request closes under the lead's words: %+v", f.cancelled)
	}
	told := false
	for _, n := range f.told {
		told = told || n.To == "w1" && n.Text == "T1 discarded by lead: PR merged"
	}
	if !told {
		t.Fatalf("the owner is told the task was closed and why: %v", f.told)
	}
	if count(t, d, "SELECT COUNT(*) FROM events WHERE kind = 'discard_task' AND agent = 'lead' AND json_extract(payload, '$.withdrew[0]') = 5") != 1 {
		t.Fatal("the record keeps which requests were withdrawn")
	}
}

func TestOnlyTheLeadOfItsTreeDiscardsOverAnOpenRequest(t *testing.T) {
	d, mk := leadEnv(t)
	for _, who := range []string{"w2", "ops"} {
		r := call(t, mk(who, nil), `{"jsonrpc":"2.0","id":2,"method":"task.discard","params":{"task":1,"reason":"x","request_id":"n1"}}`)
		if r[0]["error"] == nil {
			t.Fatalf("%s is not T1's lead and keeps today's refusal: %v", who, r[0])
		}
	}
	if count(t, d, "SELECT COUNT(*) FROM requests WHERE id = 5 AND state = 'queued'") != 1 {
		t.Fatal("a refused discard withdraws nothing")
	}
}

func TestABatchDiscardIsOneEventWithEachTasksReason(t *testing.T) {
	d, mk := leadEnv(t)
	r := call(t, mk("lead", &fakeTeller{}), `{"jsonrpc":"2.0","id":2,"method":"task.discard","params":{"tasks":[1,2],"reason":"done elsewhere",`+
		`"reasons":{"T2":"merged upstream"},"request_id":"b1"}}`)
	if r[0]["error"] != nil {
		t.Fatal(r[0])
	}
	var payload string
	if err := d.QueryRow("SELECT payload FROM events WHERE kind = 'discard_batch' AND agent = 'lead'").Scan(&payload); err != nil {
		t.Fatal("one discard_batch event: ", err)
	}
	var ev struct {
		Batch string
		Tasks []struct {
			TaskID   int64 `json:"task_id"`
			Title    string
			Reason   string
			Withdrew []int64
		}
	}
	json.Unmarshal([]byte(payload), &ev)
	if len(ev.Tasks) != 2 || ev.Tasks[0].Reason != "done elsewhere" || ev.Tasks[1].Reason != "merged upstream" || ev.Tasks[1].Title != "second" ||
		len(ev.Tasks[1].Withdrew) != 1 {
		t.Fatalf("each task with its title, reason and withdrawn requests: %s", payload)
	}
	if count(t, d, "SELECT COUNT(*) FROM events WHERE kind = 'discard_task' AND json_extract(payload, '$.batch') = '"+ev.Batch+"'") != 2 {
		t.Fatal("each task's own record names the batch")
	}
	if count(t, d, "SELECT COUNT(*) FROM tasks WHERE id = 2 AND discard_reason = 'merged upstream'") != 1 {
		t.Fatal("the reason of its own is the task's")
	}
}

func TestABatchWithATaskItCannotDiscardDiscardsNone(t *testing.T) {
	d, mk := leadEnv(t)
	r := call(t, mk("lead", nil), `{"jsonrpc":"2.0","id":2,"method":"task.discard","params":{"tasks":[1,3],"reason":"x","request_id":"b2"}}`)
	if r[0]["error"] != nil {
		t.Fatalf("T3 has no open request, so any agent may discard it: %v", r[0])
	}
	exec(t, d, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(4, 'ops work', 'ops', 'ops', 1)",
		"INSERT INTO requests(id, ts, requester, target, body, kind, state, edge_ok) VALUES(70, 1, 'ops', 'ops', 'x', 'ask', 'queued', 1)",
		"INSERT INTO task_requests(task_id, request_id) VALUES(4, 70)")
	r = call(t, mk("lead", nil), `{"jsonrpc":"2.0","id":2,"method":"task.discard","params":{"tasks":[2,4],"reason":"x","request_id":"b3"}}`)
	if r[0]["error"] == nil {
		t.Fatalf("T4 is outside lead's tree and has an open request: %v", r[0])
	}
	if count(t, d, "SELECT COUNT(*) FROM tasks WHERE id = 2 AND discarded_by IS NULL") != 1 {
		t.Fatal("a refused batch discards none of it")
	}
}

func TestAReopenedDiscardedTaskIsADraftAgain(t *testing.T) {
	d, mk := leadEnv(t)
	call(t, mk("lead", nil), `{"jsonrpc":"2.0","id":2,"method":"task.discard","params":{"tasks":[1,2],"reason":"x","request_id":"b4"}}`)
	r := call(t, mk("human", &fakeTeller{}), `{"jsonrpc":"2.0","id":2,"method":"task.reopen","params":{"task":2,"note":"still needed","request_id":"r1"}}`)
	if r[0]["error"] != nil {
		t.Fatal(r[0])
	}
	if said := r[0]["result"].(map[string]any)["said"]; said != "task T2 reopened as a draft by human: still needed - `hl task queue T2` sends it again" {
		t.Fatal(said)
	}
	if count(t, d, "SELECT COUNT(*) FROM tasks WHERE id = 2 AND discarded_by IS NULL AND discard_reason IS NULL AND queued_by IS NULL") != 1 ||
		count(t, d, "SELECT COUNT(*) FROM tasks WHERE id = 1 AND discarded_by = 'lead'") != 1 {
		t.Fatal("only the reopened task comes back")
	}
	if count(t, d, "SELECT COUNT(*) FROM events WHERE kind = 'reopen_task' AND json_extract(payload, '$.was') = 'discarded'") != 1 {
		t.Fatal("the record says it was reopened from a discard")
	}
}
