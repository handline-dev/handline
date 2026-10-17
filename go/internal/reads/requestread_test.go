package reads

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/attachments"
	"github.com/handline/handline/go/internal/rpc"
)

var (
	asFounder = &rpc.Conn{Caller: rpc.Caller{Role: "founder", Info: map[string]any{"kind": "person"}}}
	asBob     = &rpc.Conn{Caller: rpc.Caller{Role: "agent", Info: map[string]any{"kind": "agent", "name": "bob"}}}
	asCat     = &rpc.Conn{Caller: rpc.Caller{Role: "agent", Info: map[string]any{"kind": "agent", "name": "cat"}}}
)

func num64(n int64) json.Number { return json.Number(fmt.Sprint(n)) }

func read(t *testing.T, e *Env, c *rpc.Conn, id int64) map[string]any {
	t.Helper()
	got, err := e.requestRead(c, map[string]any{"id": num64(id)})
	if err != nil {
		t.Fatal(err)
	}
	return got.(map[string]any)
}

func TestRequestReadIsTheRequestAsHlReadPrintsIt(t *testing.T) {
	e := testEnv(t)
	long := "which of the two branches should land first, the one with the fix or the other"
	run(t, e, "INSERT INTO requests(id, ref, ts, requester, target, body, state, kind, edge_ok) VALUES(1, 'aaaaaaaaaaa1', 10, 'bob', 'amy', ?, 'landed', 'ask', 1)", "\n"+long)
	run(t, e, "INSERT INTO requests(id, ref, ts, requester, target, body, state, kind, edge_ok) VALUES(2, 'aaaaaaaaaaa2', 20, 'bob', 'amy', ?, 'landed', NULL, 1)", long+"\r\nand why")
	run(t, e, "INSERT INTO requests(id, ref, ts, requester, target, body, state, kind, edge_ok, parent_id, relation, response, chain_depth) "+
		"VALUES(3, 'aaaaaaaaaaa3', 30, 'amy', 'bob', 'the first', 'landed', 'say', 1, 2, 'peer', 'async', 2)")
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok, settles) VALUES(6, 60, 'bob', 'amy', 'done as asked', 'landed', 'say', 1, 3)")
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok, parent_id) VALUES(4, 40, 'amy', 'bob', 'orphan', 'queued', 'say', 1, 99)")
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok, parent_id) VALUES(5, 50, 'amy', 'bob', 'x', 'queued', 'say', 1, 1)")
	want := map[string]any{
		"id": int64(3), "ref": "aaaaaaaaaaa3", "requester": "amy", "target": "bob", "kind": "say", "state": "landed", "created": "1970-01-01T00:00:30Z",
		"relation": "peer", "response": "async", "chain_depth": int64(2),
		"parent": map[string]any{"id": int64(2), "ref": "aaaaaaaaaaa2", "line": "which of the two branches should land fi…"},
		"body":   "the first", "answer": "done as asked", "dismissal": nil,
	}
	// the founder is neither end and sees both ends, as hl read prints them
	if got := read(t, e, asFounder, 3); !reflect.DeepEqual(got, want) {
		t.Fatalf("%v", got)
	}
	if got := read(t, e, asBob, 2); got["kind"] != "ask" || got["parent"] != nil || got["answer"] != nil || got["relation"] != nil {
		t.Fatalf("a request with no kind, parent or answer: %v", got)
	}
	if got := read(t, e, asBob, 4); got["parent"] != nil {
		t.Fatalf("a parent that is gone: %v", got)
	}
	if got := read(t, e, asBob, 5); !reflect.DeepEqual(got["parent"], map[string]any{"id": int64(1), "ref": "aaaaaaaaaaa1", "line": ""}) {
		t.Fatalf("a parent whose first line is empty: %v", got["parent"])
	}
}

func TestRequestReadSaysADismissalAndNotAnAnswer(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok) VALUES"+
		"(1, 1, 'amy', 'bob', 'a', 'landed', 'ask', 1), (2, 2, 'amy', 'bob', 'b', 'landed', 'ask', 1), (3, 3, 'amy', 'bob', 'c', 'withdrawn', 'ask', 1), "+
		"(4, 4, 'amy', 'bob', 'd', 'landed', 'ask', 1)")
	run(t, e, `INSERT INTO events(ts, agent, kind, payload) VALUES(5, 'bob', 'request_reason', '{"request_id": 1, "reason": "read - no reply: not needed"}'), `+
		`(5, 'bob', 'request_reason', '{"request_id": 2, "reason": "read - no reply"}'), (5, 'amy', 'request_reason', '{"request_id": 3, "reason": "taken back"}'), `+
		`(5, 'bob', 'dismissed', '{"request_id": 4, "by": "human", "reason": "stale"}')`)
	for id, want := range map[int64]any{
		1: map[string]any{"by": nil, "reason": "not needed"},
		2: map[string]any{"by": nil, "reason": nil},
		4: map[string]any{"by": "human", "reason": "stale"},
	} {
		if got := read(t, e, asBob, id); !reflect.DeepEqual(got["dismissal"], want) || got["answer"] != nil {
			t.Errorf("#%d: %v %v", id, got["dismissal"], got["answer"])
		}
	}
	if got := read(t, e, asBob, 3); got["dismissal"] != nil || got["answer"] != "taken back" || got["state"] != "withdrawn" {
		t.Fatalf("why a withdrawn request closed is its answer: %v", got)
	}
}

func TestOnlyTheFounderReadingARequestOpensIt(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok, settles) VALUES"+
		"(1, 1, 'bob', 'human', 'a report', 'queued', 'say', 1, NULL), (2, 2, 'human', 'bob', 'a question', 'landed', 'ask', 1, NULL), "+
		"(3, 3, 'bob', 'human', 'its answer', 'landed', 'say', 1, 2)")
	// opened is whether the report is read and whether the answer to the founder's question is told
	opened := func() (bool, bool) {
		report, err := queryOne(e.DB, "SELECT read_at FROM requests WHERE id = 1")
		if err != nil {
			t.Fatal(err)
		}
		answer, err := queryOne(e.DB, "SELECT delivered_at FROM requests WHERE id = 3")
		if err != nil {
			t.Fatal(err)
		}
		return report.set("read_at"), answer.set("delivered_at")
	}
	asMember := &rpc.Conn{Caller: rpc.Caller{Role: "member", Info: map[string]any{"kind": "person"}}}
	for _, c := range []*rpc.Conn{asBob, asMember} {
		read(t, e, c, 1)
		read(t, e, c, 2)
		if r, told := opened(); r || told {
			t.Fatalf("a %s reading them opened them: %v %v", c.Caller.Role, r, told)
		}
	}
	read(t, e, asFounder, 1)
	if r, told := opened(); !r || told {
		t.Fatalf("the founder read the report alone: %v %v", r, told)
	}
	read(t, e, asFounder, 2)
	if _, told := opened(); !told {
		t.Fatal("the founder read the question they asked, so its answer is told")
	}
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok, settles) VALUES"+
		"(4, 4, 'bob', 'amy', 'between agents', 'landed', 'ask', 1, NULL), (5, 5, 'amy', 'bob', 'its answer', 'landed', 'say', 1, 4)")
	read(t, e, asFounder, 4)
	if told, err := queryOne(e.DB, "SELECT delivered_at FROM requests WHERE id = 5"); err != nil || told.set("delivered_at") {
		t.Fatalf("the founder read a question they did not ask, and its answer was marked told: %v %v", told, err)
	}
}

func TestAWaitThatEndsUnsettledOpensNothing(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok) VALUES(1, 1, 'bob', 'human', 'which one?', 'queued', 'ask', 1)")
	isRead := func() bool {
		r, err := queryOne(e.DB, "SELECT read_at FROM requests WHERE id = 1")
		if err != nil {
			t.Fatal(err)
		}
		return r.set("read_at")
	}
	got, err := e.requestRead(asFounder, map[string]any{"id": num64(1), "wait_s": num64(1)})
	if err != nil || got.(map[string]any)["state"] != "queued" || isRead() {
		t.Fatalf("the wait ran out on a queued request, and it was opened: %v %v", got, err)
	}
	if read(t, e, asFounder, 1); !isRead() {
		t.Fatal("read with no wait, the queued request is shown and so opened")
	}
	run(t, e, "UPDATE requests SET read_at = NULL, state = 'landed' WHERE id = 1")
	if _, err := e.requestRead(asFounder, map[string]any{"id": num64(1), "wait_s": num64(1)}); err != nil || !isRead() {
		t.Fatalf("a wait that ends settled is shown and so opened: %v", err)
	}
}

func TestRequestReadRefusesWhatTheReaderMayNotSeeAndBadParams(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok) VALUES(1, 1, 'amy', 'bob', 'unchecked', 'queued', 'ask', 0)")
	var re *rpc.Error
	if _, err := e.requestRead(asBob, map[string]any{"id": num64(1)}); !errors.As(err, &re) || re.RPCCode != rpc.NotFound {
		t.Fatalf("its target, before the routing table passed it: %v", err)
	}
	if got := read(t, e, asFounder, 1); got["state"] != "queued" {
		t.Fatalf("the founder reads it: %v", got)
	}
	if _, err := e.requestRead(asBob, map[string]any{"id": num64(9)}); !errors.As(err, &re) || re.RPCCode != rpc.NotFound {
		t.Fatalf("no such request: %v", err)
	}
	for _, p := range []map[string]any{{"id": "one"}, {"id": num64(1), "wait_s": num64(601)}, {}} {
		if _, err := e.requestRead(asBob, p); !errors.As(err, &re) || re.RPCCode != rpc.InvalidParams {
			t.Fatalf("%v: %v", p, err)
		}
	}
}

func TestAMessagesImagesReadBackInPartsWithAPathOnlyForAnAgent(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "bob", "")
	s := attachments.New(e.Home)
	var ids []string
	for range 2 {
		b := []byte("\x89PNG\r\n\x1a\n" + strings.Repeat("x", 100))
		out, err := s.Put(e.DB, attachments.PutIn{Name: "s.png", Mime: "image/png", Bytes: int64(len(b)), Data: b})
		if err != nil {
			t.Fatal(err)
		}
		ids = append(ids, out.Attachment.ID)
	}
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok) VALUES(1, 10, 'human', 'bob', ?, 'landed', 'say', 1)",
		"see ![it](attachment:"+ids[0]+") here")
	listed, _ := s.Lookup(ids[1])
	attachments.Record(e.DB, 1, []attachments.Info{listed})
	got, err := e.chatMessages(nil, map[string]any{"agent": "bob", "scope": "mine"})
	if err != nil {
		t.Fatal(err)
	}
	m := got.(map[string]any)["messages"].([]any)[0].(map[string]any)
	var kinds []string
	for _, p := range m["parts"].([]any) {
		part := p.(map[string]any)
		if _, ok := part["path"]; ok {
			t.Fatalf("a person gets no path: %v", part)
		}
		if img, ok := part["image"].(map[string]any); ok {
			kinds = append(kinds, img["id"].(string))
		} else {
			kinds = append(kinds, "text")
		}
	}
	if want := []string{"text", ids[0], "text", ids[1]}; !reflect.DeepEqual(kinds, want) || len(m["attachments"].([]any)) != 2 {
		t.Fatalf("parts %v, attachments %v", kinds, m["attachments"])
	}
	named, _ := s.Lookup(ids[0])
	if parts, _ := read(t, e, asBob, 1)["parts"].([]any); len(parts) != 4 || parts[1].(map[string]any)["path"] != s.Path(named) {
		t.Fatalf("an agent reading the message gets the path: %v", parts)
	}
}

func TestRequestReadIsForTheEndsTheirSupervisorsTheTaskAndTheFounder(t *testing.T) {
	e := testEnv(t)
	os.WriteFile(filepath.Join(e.Home, "team.yaml"), []byte("agents:\n  amy: {}\n  amy.kid: {}\n  bob: {}\n  cat: {}\n  dan: {}\n  eve: {}\n"), 0o644)
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok) VALUES(1, 10, 'amy.kid', 'bob', 'private', 'landed', 'ask', 1)")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 't', 'dan', 'eve', 1)")
	run(t, e, "INSERT INTO task_requests(task_id, request_id) VALUES(1, 1)")
	agent := func(name string) *rpc.Conn {
		return &rpc.Conn{Caller: rpc.Caller{Role: "agent", Info: map[string]any{"kind": "agent", "name": name}}}
	}
	for who, conn := range map[string]*rpc.Conn{"founder": asFounder, "requester": agent("amy.kid"), "target": agent("bob"),
		"supervisor of the requester": agent("amy"), "assignee of a linked task": agent("dan"), "opener of a linked task": agent("eve")} {
		if got, err := e.requestRead(conn, map[string]any{"id": num64(1)}); err != nil || got.(map[string]any)["body"] != "private" {
			t.Errorf("%s: %v %v", who, got, err)
		}
		if _, err := e.inboxGet(conn, map[string]any{"id": num64(1)}); err != nil {
			t.Errorf("%s: inbox.get: %v", who, err)
		}
	}
	// a stranger is told what a missing number is told
	_, missing := e.requestRead(asCat, map[string]any{"id": num64(99)})
	for _, f := range []func(*rpc.Conn, map[string]any) (any, error){e.requestRead, e.inboxGet} {
		_, err := f(asCat, map[string]any{"id": num64(1)})
		var nf, want *rpc.Error
		if !errors.As(err, &nf) || !errors.As(missing, &want) || nf.Code != "not_found" || nf.Code != want.Code ||
			strings.ReplaceAll(nf.Message, "1", "9") != strings.ReplaceAll(want.Message, "99", "9") {
			t.Errorf("a stranger: %v, a missing number: %v", err, missing)
		}
	}
}

func TestRequestReadKeepsTheParentsWordsFromAStranger(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok) VALUES(1, 10, 'amy', 'bob', 'private', 'landed', 'ask', 1)")
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok, parent_id) VALUES(2, 20, 'cat', 'amy', 'a reply', 'landed', 'say', 1, 1)")
	if got := read(t, e, asCat, 2); got["parent"] != nil {
		t.Fatalf("cat may not read the parent: %v", got["parent"])
	}
}

func TestRequestReadLetsTheTargetOfTheFoundersQuoteReadWhatWasQuoted(t *testing.T) {
	e := testEnv(t)
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok) VALUES(1, 10, 'amy', 'human', 'which one?', 'landed', 'ask', 1), (2, 11, 'amy', 'cat', 'between agents', 'landed', 'ask', 1)")
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok, parent_id) VALUES(3, 20, 'human', 'bob', 'see this', 'landed', 'say', 1, 1), (4, 21, 'cat', 'bob', 'see this', 'landed', 'say', 1, 2)")
	if got := read(t, e, asBob, 1); got["body"] != "which one?" {
		t.Fatalf("the founder quoted it to bob: %v", got)
	}
	if got := read(t, e, asBob, 3); got["parent"] == nil {
		t.Fatalf("bob sees the quoted line: %v", got)
	}
	if _, err := e.requestRead(asBob, map[string]any{"id": num64(2)}); err == nil {
		t.Fatal("another agent's quote does not open a request")
	}
}
