package chatwrites

import (
	"bufio"
	"database/sql"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"

	dbpkg "github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/supervisor"
)

const teamYAML = `teams:
  eng: [alice, bob]
agents:
  alice: {}
  bob: {}
edges:
  - { from: "*", to: "human", kind: task }
  - { from: "*", to: "*", relation: peer, response: async }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
limits:
  max_chain_depth: 4
  ask_rate_per_hour: 5
`

type auth struct{ agent string }

func (a auth) AgentOf(int) string         { return a.agent }
func (a auth) FounderKeyOK(k string) bool { return a.agent == "" && k == "founder" }

type fakeProposals struct{}

func (fakeProposals) OpenProposalOf(int64) string                             { return "" }
func (fakeProposals) Answered(dbpkg.Q, string, *dbpkg.Request, string) string { return "" }

type fakeEval struct{}

func (fakeEval) QueuedOrIdle(dbpkg.Q, string, string) error { return nil }

type rig struct {
	t    *testing.T
	db   *sql.DB
	home string
	env  *supervisor.Env
}

func newRig(t *testing.T) *rig {
	t.Helper()
	t.Setenv("HANDLINE_TEST_ROOT", t.TempDir())
	for _, k := range []string{"OCP_AGENT", "HANDLINE_AGENT"} {
		t.Setenv(k, "")
	}
	supervisor.RunInline = true
	t.Cleanup(func() { supervisor.RunInline = false })
	home := t.TempDir()
	os.WriteFile(filepath.Join(home, "team.yaml"), []byte(teamYAML), 0o644)
	for _, a := range []string{"alice", "bob"} {
		os.MkdirAll(filepath.Join(home, "agents", a), 0o755)
		os.WriteFile(filepath.Join(home, "agents", a, "agent.yaml"), []byte("vendor: claude\n"), 0o644)
	}
	d, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	env := &supervisor.Env{DB: d, Home: home, Log: func(string, ...any) {}, Ports: supervisor.Ports{Proposals: fakeProposals{}, Evaluate: fakeEval{}}}
	return &rig{t, d, home, env}
}

func (r *rig) server(agent string) *rpc.Server {
	s := rpc.NewServer(auth{agent}, "x", store.LatestVersion(), "local", func(string, ...any) {})
	s.DB = r.db
	Register(s, r.env, r.env)
	return s
}

// call serves one connection to srv and returns the replies to the calls given.
func (r *rig) call(srv *rpc.Server, calls ...string) []map[string]any {
	r.t.Helper()
	cl, sv := net.Pipe()
	done := make(chan struct{})
	go func() { srv.Serve(rpc.NewLineTransport(sv), 0); sv.Close(); close(done) }()
	defer func() { cl.Close(); <-done }()
	rd := bufio.NewReader(cl)
	cl.SetDeadline(time.Now().Add(10 * time.Second))
	var out []map[string]any
	for _, line := range append([]string{`{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t","protocol":1,"key":"founder"}}`}, calls...) {
		if _, err := cl.Write([]byte(line + "\n")); err != nil {
			r.t.Fatal(err)
		}
		b, err := rd.ReadBytes('\n')
		if err != nil {
			r.t.Fatal(err)
		}
		var m map[string]any
		json.Unmarshal(b, &m)
		out = append(out, m)
	}
	return out[1:]
}

func (r *rig) one(srv *rpc.Server, method, params string) map[string]any {
	r.t.Helper()
	return r.call(srv, fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":%q,"params":%s}`, method, params))[0]
}

func (r *rig) result(m map[string]any) map[string]any {
	r.t.Helper()
	res, ok := m["result"].(map[string]any)
	if !ok {
		r.t.Fatalf("not a result: %v", m)
	}
	return res
}

func (r *rig) refusedWith(m map[string]any, gate string) {
	r.t.Helper()
	e, _ := m["error"].(map[string]any)
	data, _ := e["data"].(map[string]any)
	if data["code"] != "refused" || (gate != "" && data["gate"] != gate) {
		r.t.Fatalf("want refused/%s: %v", gate, m)
	}
}

func (r *rig) count(q string, args ...any) int {
	r.t.Helper()
	var n int
	if err := r.db.QueryRow(q, args...).Scan(&n); err != nil {
		r.t.Fatal(err)
	}
	return n
}

func (r *rig) request(requester, target, body string) int64 {
	r.t.Helper()
	rid, err := dbpkg.AddRequest(r.db, dbpkg.NewRequest{Requester: requester, Target: target, Body: body, Response: "async", Kind: "ask"})
	if err != nil {
		r.t.Fatal(err)
	}
	return rid
}

func TestSendWritesTheMessageOnceAndRefusesWhatIsNotASend(t *testing.T) {
	r := newRig(t)
	s := r.server("")
	send := `{"agent":"alice","body":"hello  ","request_id":"s1"}`
	first := r.result(r.one(s, "chat.send", send))
	again := r.result(r.one(s, "chat.send", send))
	if first["accepted"] != true || again["replayed"] != true || first["request"] != again["request"] {
		t.Fatalf("a retry answers what the first did: %v %v", first, again)
	}
	var requester, target, body, state string
	r.db.QueryRow("SELECT requester, target, body, state FROM requests").Scan(&requester, &target, &body, &state)
	if r.count("SELECT COUNT(*) FROM requests") != 1 || requester != "human" || target != "alice" || body != "hello" || state != "queued" {
		t.Fatalf("one message from the person to alice: %s %s %q %s", requester, target, body, state)
	}
	if r.count("SELECT COUNT(*) FROM write_effects WHERE method = 'chat.send'") != 1 {
		t.Fatal("the effect is recorded with the write")
	}
	for _, c := range []struct{ params, code, field string }{
		{`{"agent":"nobody","body":"hi","request_id":"s2"}`, "not_found", ""},
		{`{"agent":"alice","body":" ","request_id":"s3"}`, "bad_request", "body"},
		{`{"agent":"alice","body":"x","parent":"1","request_id":"s4"}`, "bad_request", "request"},
	} {
		e, _ := r.one(s, "chat.send", c.params)["error"].(map[string]any)
		data, _ := e["data"].(map[string]any)
		if data["code"] != c.code || (c.field != "" && data["field"] == nil) {
			t.Fatalf("%s: %v", c.params, e)
		}
	}
	if r.count("SELECT COUNT(*) FROM requests") != 1 {
		t.Fatal("a refused send writes no message")
	}
}

func TestSendWithATaskOpensItWithItsBriefOnceAndALostAnswerIsReplayed(t *testing.T) {
	r := newRig(t)
	s := r.server("")
	send := `{"agent":"alice","body":"  do the thing\nin full ","task":{"title":"the thing","time_box":"2h"},"request_id":"k1"}`
	first := r.result(r.one(s, "chat.send", send))
	if first["accepted"] != true || first["request"] == nil {
		t.Fatal(first)
	}
	var tid int64
	var title, owner, opener string
	var timeout int64
	if err := r.db.QueryRow("SELECT id, title, owner, opened_by, COALESCE(timeout_s, 0) FROM tasks").Scan(&tid, &title, &owner, &opener, &timeout); err != nil {
		t.Fatal(err)
	}
	if title != "the thing" || owner != "alice" || opener != "human" || timeout != 7200 {
		t.Fatalf("the task: %q %s %s %d", title, owner, opener, timeout)
	}
	if r.count("SELECT COUNT(*) FROM task_requests WHERE task_id = ? AND request_id = ?", tid, int64(first["request"].(float64))) != 1 {
		t.Fatal("the brief is the task's first request")
	}
	again := r.result(r.one(s, "chat.send", send))
	if again["replayed"] != true || r.count("SELECT COUNT(*) FROM tasks") != 1 {
		t.Fatalf("a retry opens no second task: %v", again)
	}
	// A refusal opens nothing: no body once trimmed leaves no brief, and a time box over the longest is a bad request.
	e, _ := r.one(s, "chat.send", `{"agent":"alice","body":"x","task":{"title":"t","time_box":"9h"},"request_id":"k2"}`)["error"].(map[string]any)
	if e["data"].(map[string]any)["field"] != "time_box" || r.count("SELECT COUNT(*) FROM tasks") != 1 {
		t.Fatalf("a time box over the longest: %v", e)
	}
}

func TestSendUnderAQuestionAnswersItAndAnAnswerCanBeRepliedTo(t *testing.T) {
	r := newRig(t)
	s := r.server("")
	asked := r.request("alice", "human", "may I?")
	res := r.result(r.one(s, "chat.send", fmt.Sprintf(`{"agent":"alice","body":"yes","parent":%d,"request_id":"u1"}`, asked)))
	if res["request"] != float64(asked) {
		t.Fatalf("it answers the message it replies to: %v", res)
	}
	var state, verdict string
	r.db.QueryRow("SELECT state, COALESCE((SELECT body FROM requests a WHERE a.settles = requests.id), '') FROM requests WHERE id = ?", asked).Scan(&state, &verdict)
	if state != "landed" || verdict != "yes" {
		t.Fatalf("the question is answered: %s %q", state, verdict)
	}
	r.refusedWith(r.one(s, "chat.send", fmt.Sprintf(`{"agent":"bob","body":"yes","parent":%d,"request_id":"u2"}`, asked)), "state")
}

func TestAnswerClosesTheQuestionAndRefusesWhatAsksNothing(t *testing.T) {
	r := newRig(t)
	s := r.server("")
	asked := r.request("alice", "human", "may I?")
	ans := fmt.Sprintf(`{"request":%d,"text":"go ahead","request_id":"a1"}`, asked)
	if res := r.result(r.one(s, "chat.answer", ans)); res["accepted"] != true {
		t.Fatal(res)
	}
	if res := r.result(r.one(s, "chat.answer", ans)); res["replayed"] != true {
		t.Fatalf("a retry repeats nothing: %v", res)
	}
	if r.count("SELECT COUNT(*) FROM requests WHERE settles = ? AND body = 'go ahead'", asked) != 1 {
		t.Fatal("one answer")
	}
	r.refusedWith(r.one(s, "chat.answer", fmt.Sprintf(`{"request":%d,"text":"again","request_id":"a2"}`, r.request("human", "alice", "hi"))), "state")
	if e := r.one(s, "chat.answer", `{"request":999999,"text":"x","request_id":"a3"}`)["error"].(map[string]any); e["data"].(map[string]any)["code"] != "not_found" {
		t.Fatal(e)
	}
}

func TestAnswerOnADecisionCardDecidesItsTask(t *testing.T) {
	r := newRig(t)
	s := r.server("")
	card := r.request("alice", "human", "which one?")
	tid, err := dbpkg.NewTask(r.db, "which one?", "human", "alice", 0, dbpkg.DefaultPriority, "decision", 0)
	if err != nil {
		t.Fatal(err)
	}
	dbpkg.LinkRequest(r.db, tid, card)
	res := r.result(r.one(s, "chat.answer", fmt.Sprintf(`{"request":%d,"text":"the first","request_id":"d1"}`, card)))
	if res["said"] != fmt.Sprintf("T%d decided", tid) {
		t.Fatal(res)
	}
	if r.count("SELECT COUNT(*) FROM tasks WHERE id = ? AND done_evidence = 'the first'", tid) != 1 {
		t.Fatal("the answer is the task's evidence")
	}
}

func TestDecideMarksATaskDoneWhenItsCardWasClosedSomeOtherWay(t *testing.T) {
	r := newRig(t)
	gone := r.request("alice", "human", "which one?")
	dbpkg.MarkDelivered(r.db, gone)
	r.db.Exec("UPDATE requests SET state = 'landed' WHERE id = ?", gone)
	tid, err := dbpkg.NewTask(r.db, "which one?", "human", "alice", 0, dbpkg.DefaultPriority, "decision", 0)
	if err != nil {
		t.Fatal(err)
	}
	dbpkg.LinkRequest(r.db, tid, gone)
	tx, err := r.db.Begin()
	if err != nil {
		t.Fatal(err)
	}
	var note string
	res, err := decide(r.env, r.env, tx, tid, "the second", func(_ dbpkg.Q, n string) error { note = n; return nil })
	if err != nil || !res.OK {
		t.Fatalf("%v %+v", err, res)
	}
	if err := tx.Commit(); err != nil {
		t.Fatal(err)
	}
	if res.Tell() != fmt.Sprintf("T%d decided", tid) || note != fmt.Sprintf("task T%d done (the second)", tid) {
		t.Fatalf("%q %q", res.Tell(), note)
	}
	if r.count("SELECT COUNT(*) FROM tasks WHERE id = ? AND done_evidence = 'the second'", tid) != 1 {
		t.Fatal("the decision is the task's evidence, though no open card settled it")
	}
}

func TestAnswerOnADecisionCardRefusesWhileTheTaskHasAnOpenChild(t *testing.T) {
	r := newRig(t)
	s := r.server("")
	card := r.request("alice", "human", "which one?")
	tid, _ := dbpkg.NewTask(r.db, "which one?", "human", "alice", 0, dbpkg.DefaultPriority, "decision", 0)
	dbpkg.LinkRequest(r.db, tid, card)
	dbpkg.NewTask(r.db, "first this", "alice", "alice", tid, dbpkg.DefaultPriority, "report", 0)
	r.refusedWith(r.one(s, "chat.answer", fmt.Sprintf(`{"request":%d,"text":"x","request_id":"d3"}`, card)), "")
	if r.count("SELECT COUNT(*) FROM tasks WHERE id = ? AND done_evidence IS NOT NULL", tid) != 0 || r.count("SELECT COUNT(*) FROM requests WHERE id = ? AND state = 'queued'", card) != 1 {
		t.Fatal("a refused decision writes nothing")
	}
}

func TestDismissTakesARowOffWithoutAnAnswer(t *testing.T) {
	r := newRig(t)
	s := r.server("")
	asked := r.request("alice", "human", "fyi?")
	res := r.result(r.one(s, "chat.dismiss", fmt.Sprintf(`{"request":%d,"request_id":"x1"}`, asked)))
	if res["accepted"] != true {
		t.Fatal(res)
	}
	var state string
	r.db.QueryRow("SELECT state FROM requests WHERE id = ?", asked).Scan(&state)
	if state != "landed" || r.count("SELECT COUNT(*) FROM requests WHERE settles = ?", asked) != 0 || r.count("SELECT COUNT(*) FROM events WHERE kind = 'dismissed'") != 1 {
		t.Fatalf("closed as dismissed, with no answer: %s", state)
	}
	r.refusedWith(r.one(s, "chat.dismiss", fmt.Sprintf(`{"request":%d,"request_id":"x2"}`, r.request("alice", "bob", "x"))), "state")
}

func TestCancelNeedsAConfirmOnceDeliveredAndClosesAsRejected(t *testing.T) {
	r := newRig(t)
	s := r.server("")
	rid := r.request("human", "alice", "do the thing")
	dbpkg.MarkDelivered(r.db, rid)
	e := r.one(s, "chat.cancel", fmt.Sprintf(`{"request":%d,"request_id":"c1"}`, rid))["error"].(map[string]any)
	data := e["data"].(map[string]any)
	if data["gate"] != "confirm" || data["plan"] == nil {
		t.Fatalf("a delivered message is confirmed first: %v", e)
	}
	if r.count("SELECT COUNT(*) FROM requests WHERE id = ? AND state = 'queued'", rid) != 1 {
		t.Fatal("nothing was closed")
	}
	res := r.result(r.one(s, "chat.cancel", fmt.Sprintf(`{"request":%d,"confirm":true,"request_id":"c2"}`, rid)))
	if res["accepted"] != true {
		t.Fatal(res)
	}
	var state string
	r.db.QueryRow("SELECT state FROM requests WHERE id = ?", rid).Scan(&state)
	if state != "landed" || r.count("SELECT COUNT(*) FROM requests WHERE settles = ? AND body = ?", rid, supervisor.CancelVerdict) != 1 {
		t.Fatalf("closed with the cancel verdict as its answer: %s", state)
	}
	// an undelivered message is simply withdrawn
	plain := r.request("human", "bob", "never mind")
	r.result(r.one(s, "chat.cancel", fmt.Sprintf(`{"request":%d,"request_id":"c3"}`, plain)))
	r.db.QueryRow("SELECT state FROM requests WHERE id = ?", plain).Scan(&state)
	if state != "withdrawn" {
		t.Fatalf("withdrawn: %s", state)
	}
	r.refusedWith(r.one(s, "chat.cancel", fmt.Sprintf(`{"request":%d,"request_id":"c4"}`, r.request("alice", "bob", "x"))), "state")
}

func TestSeenMovesTheMarkAndReadsWhatIsBehindIt(t *testing.T) {
	r := newRig(t)
	s := r.server("")
	a, b := r.request("alice", "human", "one"), r.request("alice", "human", "two")
	res := r.result(r.one(s, "chat.seen", fmt.Sprintf(`{"agent":"alice","up_to":%d,"request_id":"n1"}`, a)))
	if res["accepted"] != true {
		t.Fatal(res)
	}
	if r.count("SELECT COUNT(*) FROM requests WHERE id = ? AND read_at IS NOT NULL", a) != 1 || r.count("SELECT COUNT(*) FROM requests WHERE id = ? AND read_at IS NOT NULL", b) != 0 {
		t.Fatal("read up to the mark and no further")
	}
	r.result(r.one(s, "chat.seen", `{"agent":"alice","up_to":1,"request_id":"n2"}`))
	if c, _ := dbpkg.Cursor(r.db, "for_you_seen:alice"); c != a {
		t.Fatalf("the mark never moves back: %d", c)
	}
}

func TestSendNowMarksTheMessageAndRefusesOneThatIsNoLongerWaiting(t *testing.T) {
	r := newRig(t)
	s := r.server("")
	rid := r.request("human", "alice", "now please")
	call := fmt.Sprintf(`{"request":%d,"request_id":"j1"}`, rid)
	if res := r.result(r.one(s, "chat.send_now", call)); res["said"] != fmt.Sprintf("message %d is told to alice now", rid) {
		t.Fatal(res)
	}
	if r.count("SELECT COUNT(*) FROM requests WHERE id = ? AND send_now IS NOT NULL", rid) != 1 {
		t.Fatal("marked send-now")
	}
	dbpkg.MarkDelivered(r.db, rid)
	r.refusedWith(r.one(s, "chat.send_now", fmt.Sprintf(`{"request":%d,"request_id":"j2"}`, rid)), "")
	e := r.one(s, "chat.send_now", fmt.Sprintf(`{"request":%d,"request_id":"j3"}`, r.request("alice", "bob", "x")))["error"].(map[string]any)
	if e["data"].(map[string]any)["code"] != "not_found" {
		t.Fatal(e)
	}
}

func TestADeniedSendKeepsItsRowAndAnswersRefused(t *testing.T) {
	r := newRig(t)
	os.WriteFile(filepath.Join(r.home, "team.yaml"), []byte(teamYAML+"# no edge from the person\n"), 0o644)
	// the person's edge is removed: the routing denies, and the denied row is kept
	y := `teams:
  eng: [alice]
agents:
  alice: {}
edges:
  - { from: alice, to: human, kind: task }
limits: { max_chain_depth: 4, ask_rate_per_hour: 5 }
`
	os.WriteFile(filepath.Join(r.home, "team.yaml"), []byte(y), 0o644)
	e := r.one(r.server(""), "chat.send", `{"agent":"alice","body":"hi","request_id":"k1"}`)
	r.refusedWith(e, "")
	if r.count("SELECT COUNT(*) FROM requests WHERE state = 'denied'") != 1 {
		t.Fatal("the denied message stays as a row")
	}
}
