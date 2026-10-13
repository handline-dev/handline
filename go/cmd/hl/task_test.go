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
		{"", []string{"task", "new", "--assign", "b.two", "-t", "90m", "  A  title ", "  the body  ", "--parent", "T5",
			"--blocked-by=t6", "--priority", "p1", "--expects", "SHA"},
			[]string{`task.new {"blocked_by":6,"body":"the body","owner":"b.two","parent":5,"priority":1,"time_box":"5400s","title":"A  title"}`}},
		{"from stdin\n", []string{"task", "new", "--assign", "human", "-t", "2h", "x", "--file", "-"},
			[]string{`task.new {"body":"from stdin","owner":"human","time_box":"7200s","title":"x"}`}},
		{"", []string{"task", "new", "--assign", "b.two", "-t", "1h", "x", "--owner", "human"},
			[]string{`task.new {"owner":"b.two","reporter":"human","time_box":"3600s","title":"x"}`}},
		{"", []string{"task", "new", "--assign", "b.two", "-t", "1h", "x"},
			[]string{`task.new {"owner":"b.two","time_box":"3600s","title":"x"}`}},
		{"", []string{"task", "new", "--assign", "b.two", "x"}, []string{`task.new {"owner":"b.two","title":"x"}`}},
		{"", []string{"task", "begin", "t", "--chat", "7", "--why", "w"},
			[]string{`task.begin {"expects":"report","message":7,"title":"t","why":"w"}`}},
		{"", []string{"task", "begin", "t", "--chat", "7"},
			[]string{`task.begin {"expects":"report","message":7,"title":"t"}`}},
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
		{"new brief", []string{"task", "edit", "T3", "--body", "-", "--assign", "b.two", "--owner", "a.one", "--blocked-by", "T9"},
			[]string{`task.edit {"blocked_by":9,"body":"new brief","owner":"b.two","reporter":"a.one","task":3}`}},
		{"", []string{"task", "merge", "T1", "T2", "T3"}, []string{`task.merge {"into":1,"tasks":[2,3]}`}},
		{"", []string{"task", "done", "T3", "abc", "--reason", "--repo", "./a//b/"},
			[]string{`task.done {"evidence":"abc","reason":true,"repo":"a/b","task":3}`}},
		{"merged as abc\nall green\n", []string{"task", "done", "T3", "-", "--repo", "/r"},
			[]string{`task.done {"evidence":"merged as abc\nall green","repo":"/r","task":3}`}},
		{"abc1234\n", []string{"task", "done", "T3", "-", "--repo", "/r"}, []string{`task.done {"evidence":"abc1234","repo":"/r","task":3}`}},
		{"", []string{"task", "done", "T3", "--request", "9"}, []string{`task.done {"repo":"` + real + `","request":9,"task":3}`}},
		{"", []string{"task", "done", "T3", "w", "--repo", "/r", "--commit", "abc1234", "--shot", "a/../b.png", "--shot=/s/c.png"},
			[]string{`task.done {"commit":"abc1234","evidence":"w","repo":"/r","shots":["` + real + `/b.png","/s/c.png"],"task":3}`}},
		{"", []string{"task", "reopen", "T3", "why"}, []string{`task.reopen {"note":"why","task":3}`}},
		{"", []string{"task", "discard", "T3", "why"}, []string{`task.discard {"reason":"why","task":3}`}},
		{"", []string{"task", "release", "T3", "why"}, []string{`task.release {"reason":"why","task":3}`}},
		{"", []string{"task", "accept", "T3"}, []string{`task.accept {"task":3}`}},
		{"", []string{"task", "reject", "T3", "no tests"}, []string{`task.reject {"note":"no tests","task":3}`}},
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
	if out, _, code := r.run(asAgent, "task", "new", "--assign", "b.two", "-t", "1h", "x", "y"); code != 0 || out != "task T42 opened\nT42\n" { // local-ref-ok
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
		{asAgent, []string{"task", "new", "--assign", "z.z", "-t", "1h", "x", "y"}, "no agent named z.z\n"},
		{asAgent, []string{"task", "new", "--assign", "b.two", "--owner", "z.z", "-t", "1h", "x", "y"}, "no agent named z.z\n"},
		{asAgent, []string{"task", "new", "--owner", "b.two", "-t", "1h", "x"},
			"a task needs --assign: who works it (--owner is who answers for it, you when not given). Try:\n  hl task new --assign b.two -t 60m x\n"},
		{asAgent, []string{"task", "new", "-t", "1h", "x"},
			"a task needs --assign: who works it (--owner is who answers for it, you when not given). Try:\n  hl task new --assign <agent> -t 60m x\n"},
		{asAgent, []string{"task", "new", "--assign", "b.two", "-t", "90s", " ", "y"},
			"a task needs a title. Try:\n  hl task new --assign b.two -t 90s '<title>' y\n"},
		{asAgent, []string{"task", "handoff", "T3", "b.two"}, "note is required (inline, --file PATH, or on stdin)\n"},
		{asAgent, []string{"task", "done", "T3", "-"}, "evidence is required, and stdin (-) was empty\n"},
		{asAgent, []string{"task", "handoff", "T3", "b.two", "n", "--file", "f"}, "give note inline or with --file, not both\n"},
		{asAgent, []string{"task", "edit", "T3", "--owner", "b.two"}, "--owner is who answers for a task, not who works it, so it needs --assign too. To move the work:\n" +
			"  hl task edit T3 --assign b.two\nTo change only who answers for it, name the assignee it keeps:\n  hl task edit T3 --owner b.two --assign <its assignee>\n"},
		{asAgent, []string{"task", "edit", "T3"}, "edit needs a new assignee, a new owner, a new title, --body, --priority, --blocked-by, --unblock, or --timeout\n"},
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
		{"task", "new", "--assi", "b.two", "-t", "1h", "x"}, {"task", "new", "--assign", "b", "-t", "5h", "x"},
		{"task", "new", "--assign", "b", "-t", "1h", "x", "--reply", "1", "hi"}, {"task", "new", "--assign", "b", "-t", "1h", "x", "--", "y"},
		{"task", "list", "--state", "nope"}, {"task", "begin", "x", "--chat", "a", "--why", "w"}, {"task", "begin", "x", "--why", "w"}, {"task", "begin", "x"},
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

func TestAReportedTaskReadsAsAwaitingWhoItAnswersTo(t *testing.T) {
	r := agentsRig(t)
	task := `"id":7,"title":"Fix it","state":"reported","to":"b.two","from":"a.one","asker":"c","priority":2,"parent":null,"hold_reason":"","mismatch":""`
	r.daemon(map[string]string{
		"task.get": `{` + task + `,"created":"2026-10-01T10:00:00Z","time":null,"evidence":null,"done":null,"discarded":null,"body":"",
			"report":{"by":"b.two","evidence":"built","at":"2026-10-02T11:30:00Z"},"memos":[]}`,
		"tasks.list": `{"items":[{` + task + `,"evidence":null,"block_reason":"","last_activity":"2026-10-01T10:00:00Z","requests":[]}],"more":false}`,
		"task.board": `{"mine":[{"task":{` + task + `},"now":{"kind":"none","tasks":[],"agents":[]}}],"reports":[]}`,
	})
	show := "T7  Fix it\nstate reported, awaiting c   assignee b.two   owner c   opened by a.one   P2   created 2026-10-01 10:00\n" +
		"reported 2026-10-02 11:30: built\n\nbrief\n(none)\n"
	if out, _, code := r.run(asAgent, "task", "show", "T7"); code != 0 || out != show {
		t.Errorf("show: %d\n%q", code, out)
	}
	if out, _, _ := r.run(asAgent, "task", "list"); !strings.HasPrefix(out, "T7    Fix it  [b.two] reported, awaiting c  P2") {
		t.Errorf("list: %q", out)
	}
	if out, _, _ := r.run(asAgent, "task", "board"); out != "T7    Fix it\n      assignee b.two  owner c  now: reported, awaiting c\n" {
		t.Errorf("board: %q", out)
	}
}

func TestTaskShowListAndBoardRead(t *testing.T) {
	r := agentsRig(t)
	calls := r.daemon(map[string]string{
		"task.get": `{"id":7,"title":"Fix it","state":"done","to":"b.two","from":"a.one","priority":1,"parent":3,
			"created":"2026-10-01T10:00:00Z","time":{"length_s":3600,"default":false,"start":null,"end":null,"overdue_since":null,"told":0},
			"evidence":"abc123","done":"2026-10-02T11:30:00Z","discarded":null,"body":"the brief",
			"result":{"commit":"abc1234def","on_main":false,"shots":[{"n":1,"bytes":2048},{"n":2,"bytes":10}]},
			"memos":[{"id":5,"author":"a.one","at":"2026-10-01T12:00:00Z","text":"a memo","edited_at":null}],
			"blockers":[{"task":9,"owner":"c","title":"first"}],"subtasks":[{"id":8,"owner":"d","state":"doing","title":"child"}],
			"handoffs":[{"at":"2026-10-01T13:00:00Z","from":"a.one","to":"b.two","note":"over to you"}],
			"assignments":[{"at":"2026-10-01T14:00:00Z","by":"c","what":"owner","from":"a.one","to":"c"}]}`,
		"tasks.list": `{"items":[{"id":7,"title":"Fix it","to":"b.two","state":"doing","parent":null,"edited_by":null,"evidence":null,
			"discarded":null,"block_reason":"waiting: x","last_activity":"2026-10-01T10:00:00Z","priority":2,"asker":"a.one",
			"mismatch":"","hold_reason":"","requests":[{"id":11,"from":"a.one","to":"b.two","state":"queued","at":"2026-10-01T10:00:00Z"}]}],
			"more":false,"reason_gaps":{"retirements_blank":1,"retirements":4,"closures_sha":2,"closures_request":0,"closures_reason":3}}`,
		"task.board": `{"mine":[{"task":{"id":7,"title":"Fix it","to":"b.two","asker":"a.one","priority":2,"parent":3,"hold_reason":"","mismatch":""},
			"now":{"kind":"held_as_sub_task","tasks":[8],"agents":["d"]}}],"reports":[]}`,
	})
	show := `T7  Fix it
state done   assignee b.two   owner a.one   P1   parent T3   created 2026-10-01 10:00
timeout 1h00m - counts while the task is doing
done 2026-10-02 11:30: abc123
commit abc1234def (not on main)
shot ` + r.home + `/shots/T7/1.png (2048 bytes)
shot ` + r.home + `/shots/T7/2.png (10 bytes)

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

assignee and owner changes
  2026-10-01 14:00  owner a.one -> c, by c
`
	if out, _, code := r.run(asAgent, "task", "show", "t7"); code != 0 || out != show {
		t.Errorf("show: %d\n%s", code, out)
	}
	list := "T7    Fix it  [b.two] doing  P2  owner a.one  waiting: x  active 2026-10-01 10:00\n" +
		"        request #11 a.one → b.two queued 2026-10-01 10:00\n" +
		"reason (last 30d): 1/4 retirements carry no reason, 2 closures by sha, 0 by request, 3 by reason\n"
	if out, _, code := r.run(asAgent, "task", "list", "--assign", "b.two", "--owner", "a.one", "--all"); code != 0 || out != list {
		t.Errorf("list: %d\n%s", code, out)
	}
	board := "T7    Fix it (under T3)\n      assignee b.two  owner a.one  now: held as sub-task T8 (d)\n"
	if out, _, code := r.run(asAgent, "task", "board"); code != 0 || out != board {
		t.Errorf("board: %d\n%q", code, out)
	}
	got := sent(calls())
	want := []string{`task.get {"task":7}`, `tasks.list {"all":true,"everyone":false,"limit":200,"owner":"b.two","reason_gaps":true,"reporter":"a.one"}`,
		`task.board {"everyone":false}`}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("calls\n got  %v\n want %v", got, want)
	}
}
