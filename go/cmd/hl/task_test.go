package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

var asAgent = []string{"HANDLINE_AGENT=a.one"}

// sent is each call's method and params, without the request_id a write adds.
func sent(calls []map[string]any) []string {
	var out []string
	for _, c := range calls {
		p, _ := c["params"].(map[string]any)
		delete(p, "request_id")
		b, _ := json.Marshal(p)
		out = append(out, c["method"].(string)+" "+string(b))
	}
	return out
}

func agentsRig(t *testing.T) *rig {
	r := newRig(t)
	dir := filepath.Join(r.home, "agents", "b.two")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "agent.yaml"), []byte("name: b.two\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	return r
}

func TestEachTaskWriteSendsItsMethodAndParams(t *testing.T) {
	r := agentsRig(t)
	calls := r.daemon(map[string]string{"task.new": `{"said":"opened","task":42}`})
	real, _ := filepath.EvalSymlinks(r.root)
	cases := []struct {
		stdin string
		args  []string
		want  []string
	}{
		{"", []string{"task", "new", "--owner", "b.two", "-t", "90m", "  A  title ", "  the body  ", "--parent", "T5",
			"--blocked-by=t6", "--priority", "p1", "--expects", "SHA"},
			[]string{`task.new {"blocked_by":6,"body":"the body","expects":"sha","owner":"b.two","parent":5,"priority":1,"time_box":"5400s","title":"A  title"}`}},
		{"from stdin\n", []string{"task", "new", "--owner", "human", "-t", "2h", "x", "--file", "-"},
			[]string{`task.new {"body":"from stdin","owner":"human","time_box":"7200s","title":"x"}`}},
		{"", []string{"task", "new", "--owner", "b.two", "-t", "1h", "x"},
			[]string{`task.new {"owner":"b.two","time_box":"3600s","title":"x"}`}},
		{"", []string{"task", "begin", "t", "--chat", "7", "--why", "w"},
			[]string{`task.begin {"expects":"report","message":7,"title":"t","why":"w"}`}},
		{"", []string{"task", "handoff", "T3", "b.two", "the note"}, []string{`task.handoff {"note":"the note","task":3,"to":"b.two"}`}},
		{"piped note", []string{"task", "handoff", "T3", "b.two"}, []string{`task.handoff {"note":"piped note","task":3,"to":"b.two"}`}},
		{"", []string{"task", "start", "T3"}, []string{`task.start {"task":3}`}},
		{"", []string{"task", "note", "T3", "hi"}, []string{`task.memo.add {"task":3,"text":"hi"}`}},
		{"", []string{"task", "memo", "T3", "add", "hi"}, []string{`task.memo.add {"task":3,"text":"hi"}`}},
		{"from stdin", []string{"task", "memo", "T3", "edit", "4", "-"}, []string{`task.memo.edit {"memo":4,"task":3,"text":"from stdin"}`}},
		{"", []string{"task", "memo", "T3", "delete", "4"}, []string{`task.memo.delete {"memo":4,"task":3}`}},
		{"", []string{"task", "queue", "T1", "T2"}, []string{`task.queue {"tasks":[1]}`, `task.queue {"tasks":[2]}`}},
		{"", []string{"task", "draft", "T1"}, []string{`task.draft {"tasks":[1]}`}},
		{"", []string{"task", "edit", "T3", "--title", "t", "--priority", "3", "--timeout", "2h", "--unblock", "T4"},
			[]string{`task.edit {"priority":3,"task":3,"time_box":"7200s","title":"t","unblock":4}`}},
		{"new brief", []string{"task", "edit", "T3", "--body", "-", "--owner", "b.two", "--blocked-by", "T9"},
			[]string{`task.edit {"blocked_by":9,"body":"new brief","owner":"b.two","task":3}`}},
		{"", []string{"task", "merge", "T1", "T2", "T3"}, []string{`task.merge {"into":1,"tasks":[2,3]}`}},
		{"", []string{"task", "done", "T3", "abc", "--reason", "--repo", "./a//b/"},
			[]string{`task.done {"evidence":"abc","reason":true,"repo":"a/b","task":3}`}},
		{"merged as abc\nall green\n", []string{"task", "done", "T3", "-", "--repo", "/r"},
			[]string{`task.done {"evidence":"merged as abc\nall green","repo":"/r","task":3}`}},
		{"abc1234\n", []string{"task", "done", "T3", "-", "--repo", "/r"}, []string{`task.done {"evidence":"abc1234","repo":"/r","task":3}`}},
		{"", []string{"task", "done", "T3", "--request", "9"}, []string{`task.done {"repo":"` + real + `","request":9,"task":3}`}},
		{"", []string{"task", "reopen", "T3", "why"}, []string{`task.reopen {"note":"why","task":3}`}},
		{"", []string{"task", "discard", "T3", "why"}, []string{`task.discard {"reason":"why","task":3}`}},
		{"", []string{"task", "release", "T3"}, []string{`task.release {"task":3}`}},
	}
	seen := 0
	for _, c := range cases {
		out, errs, code := r.runIn(c.stdin, asAgent, c.args...)
		all := sent(calls())
		got := all[seen:]
		seen = len(all)
		if code != 0 || errs != "" || !reflect.DeepEqual(got, c.want) {
			t.Errorf("%v: exit %d, out %q, err %q\n got  %v\n want %v", c.args, code, out, errs, got, c.want)
		}
	}
}

func TestTaskWritesSayWhatTheDaemonSaid(t *testing.T) {
	r := agentsRig(t)
	r.daemon(map[string]string{"task.new": `{"said":"task T42 opened","task":42}`, "task.start": "error:T3 is not yours", // local-ref-ok
		"task.queue": `{"said":"queued"}`})
	if out, _, code := r.run(asAgent, "task", "new", "--owner", "b.two", "-t", "1h", "x", "y"); code != 0 || out != "task T42 opened\nT42\n" { // local-ref-ok
		t.Errorf("new: %q %d", out, code)
	}
	if out, _, code := r.run(asAgent, "task", "start", "T3"); code != 1 || out != "T3 is not yours\n" {
		t.Errorf("refused: %q %d", out, code)
	}
	if out, _, code := r.run(asAgent, "task", "queue", "T1", "T2"); code != 0 || out != "queued\nqueued\n" {
		t.Errorf("queue: %q %d", out, code)
	}
}

func TestTaskRefusalsBeforeAnyCall(t *testing.T) {
	r := agentsRig(t)
	calls := r.daemon(nil)
	cases := []struct {
		env  []string
		args []string
		want string
	}{
		{asAgent, []string{"task", "new", "--owner", "z.z", "-t", "1h", "x", "y"}, "no agent named z.z\n"},
		{asAgent, []string{"task", "new", "--owner", "b.two", "it's", "a b", "--priority", "P2"},
			"a task needs -t/--timeout (90m, 2h; at most 4h00m). Try:\n  hl task new --owner b.two -t 2h 'it'\"'\"'s' 'a b' --priority P2\n"},
		{asAgent, []string{"task", "new", "--owner", "b.two", "-t", "90s", " ", "y"},
			"a task needs a title. Try:\n  hl task new --owner b.two -t 90s '<title>' y\n"},
		{asAgent, []string{"task", "handoff", "T3", "b.two"}, "note is required (inline, --file PATH, or on stdin)\n"},
		{asAgent, []string{"task", "done", "T3", "-"}, "evidence is required, and stdin (-) was empty\n"},
		{asAgent, []string{"task", "handoff", "T3", "b.two", "n", "--file", "f"}, "give note inline or with --file, not both\n"},
		{asAgent, []string{"task", "edit", "T3"}, "edit needs a new owner, a new reporter, a new title, --body, --priority, --blocked-by, --unblock, or --timeout\n"},
		{asAgent, []string{"task", "memo", "T3", "add"}, "add takes the memo text\n"},
		{asAgent, []string{"task", "memo", "T3", "edit", "4"}, "edit takes the memo id, then the new text\n"},
		{nil, []string{"task", "start", "T3"}, "cannot tell who is start a tasking this: OCP_AGENT is unset and this is not an " +
			"interactive shell. Agents are started with `hl launch <agent>`, which sets it.\n"},
	}
	for _, c := range cases {
		if out, _, code := r.run(c.env, c.args...); code != 1 || out != c.want {
			t.Errorf("%v: exit %d\n got  %q\n want %q", c.args, code, out, c.want)
		}
	}
	if got := calls(); len(got) != 0 {
		t.Fatalf("calls %v", got)
	}
}

func TestWhatTheTaskPortDoesNotParseIsPythons(t *testing.T) {
	r := agentsRig(t)
	calls := r.daemon(nil)
	for _, args := range [][]string{
		{"task"}, {"task", "frob"}, {"task", "show", "12"}, {"task", "show", "T1", "x"}, {"task", "show", "T1", "-h"},
		{"task", "new", "--own", "b.two", "-t", "1h", "x"}, {"task", "new", "-t", "1h", "x"}, {"task", "new", "--owner", "b", "-t", "5h", "x"},
		{"task", "new", "--owner", "b", "-t", "1h", "x", "--reply", "1", "hi"}, {"task", "new", "--owner", "b", "-t", "1h", "x", "--", "y"},
		{"task", "list", "--state", "nope"}, {"task", "begin", "x", "--chat", "a", "--why", "w"}, {"task", "begin", "x", "--why", "w"},
		{"task", "memo", "T1", "bogus"}, {"task", "memo", "T1", "delete", "abc"}, {"task", "done", "T1", "--request", "x"},
		{"task", "edit", "T1", "--title"}, {"task", "queue"}, {"task", "merge", "T1"},
	} {
		out, _, code := r.run(asAgent, args...)
		if code != 0 || !strings.HasPrefix(out, "python ") || !strings.HasSuffix(strings.TrimSpace(out), strings.Join(args, " ")) {
			t.Errorf("%v: %q %d", args, out, code)
		}
	}
	if got := calls(); len(got) != 0 {
		t.Fatalf("calls %v", got)
	}
}

func TestTaskShowListAndBoardRead(t *testing.T) {
	r := agentsRig(t)
	calls := r.daemon(map[string]string{
		"task.get": `{"id":7,"title":"Fix it","state":"done","to":"b.two","from":"a.one","priority":1,"parent":3,
			"created":"2026-10-01T10:00:00Z","time":{"length_s":3600,"default":false,"start":null,"end":null,"overdue_since":null,"told":0},
			"evidence":"abc123","done":"2026-10-02T11:30:00Z","discarded":null,"body":"the brief",
			"memos":[{"id":5,"author":"a.one","at":"2026-10-01T12:00:00Z","text":"a memo","edited_at":null}],
			"blockers":[{"task":9,"owner":"c","title":"first"}],"subtasks":[{"id":8,"owner":"d","state":"doing","title":"child"}],
			"handoffs":[{"at":"2026-10-01T13:00:00Z","from":"a.one","to":"b.two","note":"over to you"}]}`,
		"tasks.list": `{"items":[{"id":7,"title":"Fix it","to":"b.two","state":"doing","parent":null,"edited_by":null,"evidence":null,
			"discarded":null,"block_reason":"waiting: x","last_activity":"2026-10-01T10:00:00Z","priority":2,"asker":"a.one",
			"mismatch":"","hold_reason":"","requests":[{"id":11,"from":"a.one","to":"b.two","state":"queued","at":"2026-10-01T10:00:00Z"}]}],
			"more":false,"reason_gaps":{"retirements_blank":1,"retirements":4,"closures_sha":2,"closures_request":0,"closures_reason":3}}`,
		"task.board": `{"mine":[{"task":{"id":7,"title":"Fix it","priority":2,"parent":3,"hold_reason":"","mismatch":""},
			"now":{"kind":"held_as_sub_task","tasks":[8],"agents":["d"]}}],"reports":[]}`,
	})
	show := `T7  Fix it
state done   owner b.two   opened by a.one   P1   parent T3   created 2026-10-01 10:00
timeout 1h00m - counts while the task is doing
done 2026-10-02 11:30: abc123

brief
the brief

memos
  memo 5  a.one  2026-10-01 12:00
    a memo

waiting on
  T9 (c)  first

child tasks
  T8 (d, doing)  child

handoffs
  2026-10-01 13:00  a.one -> b.two
    over to you
`
	if out, _, code := r.run(asAgent, "task", "show", "t7"); code != 0 || out != show {
		t.Errorf("show: %d\n%s", code, out)
	}
	list := "T7    Fix it  [b.two] doing  P2  may reopen: a.one  waiting: x  active 2026-10-01 10:00\n" +
		"        request #11 a.one → b.two queued 2026-10-01 10:00\n" +
		"reason (last 30d): 1/4 retirements carry no reason, 2 closures by sha, 0 by request, 3 by reason\n"
	if out, _, code := r.run(asAgent, "task", "list", "--owner", "b.two", "--all"); code != 0 || out != list {
		t.Errorf("list: %d\n%s", code, out)
	}
	board := "T7    Fix it (under T3)\n      now: held as sub-task T8 (d)\n"
	if out, _, code := r.run(asAgent, "task", "board"); code != 0 || out != board {
		t.Errorf("board: %d\n%q", code, out)
	}
	got := sent(calls())
	want := []string{`task.get {"task":7}`, `tasks.list {"all":true,"everyone":false,"limit":200,"owner":"b.two","reason_gaps":true}`,
		`task.board {"everyone":false}`}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("calls\n got  %v\n want %v", got, want)
	}
}
