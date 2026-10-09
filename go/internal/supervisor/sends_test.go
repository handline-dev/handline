package supervisor

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
)

func init() { RunInline = true }

func (r *rig) say(in SayVerbIn) *SayVerbOut {
	r.t.Helper()
	out, err := r.SayVerb(r.DB, in)
	r.must(err)
	out.After()
	return out
}

func TestSayVerbQueuesAndTypesAtOnce(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	out := r.say(SayVerbIn{Requester: "alice", Target: "bob", Body: "psst"})
	if !out.OK || !strings.HasPrefix(out.Msg, "queued as #1 for bob (response=async)") {
		t.Fatalf("%+v", out)
	}
	if !strings.Contains(r.term.last(), "#1 from alice, waiting for you:\npsst") {
		t.Fatalf("%q", r.term.last())
	}
	if out := r.say(SayVerbIn{Target: "bob", Body: "x"}); out.OK || out.Msg != noWho {
		t.Fatalf("%+v", out)
	}
}

func TestSayToAReportWithoutATaskIsRefusedWithTheTaskThatWouldReachIt(t *testing.T) {
	r := newRig(t)
	// bob reports to alice by a supervisor edge; written before the table is first read and sealed
	os.WriteFile(filepath.Join(r.home, "team.yaml"), []byte(strings.Replace(teamYAML, "edges:\n", "edges:\n  - { from: alice, to: bob, relation: supervisor, response: async }\n", 1)), 0o644)
	out := r.say(SayVerbIn{Requester: "alice", Target: "bob", Body: "fix the bug"})
	want := "work cannot reach a report by message: make it a task (`hl task new`), or say --task T<n> to memo one. Try:\n  hl task new --owner bob -t 2h '<title>' 'fix the bug'"
	if out.OK || out.Msg != want {
		t.Fatalf("%q", out.Msg)
	}
}

func TestSayWithATaskIsThatTasksMemoAndTellsTheHolder(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	tid := r.newTask("bob", "alice", "job")
	r.AdvanceCurrentTask(r.DB, "bob")
	r.term.typed = nil
	out := r.say(SayVerbIn{Requester: "alice", Target: "bob", Body: "check the cache", Task: tid})
	if !out.OK || out.Msg != "memo 1 added to T1" {
		t.Fatalf("%+v", out)
	}
	if !strings.Contains(r.term.last(), "T1 memo added by alice: check the cache") {
		t.Fatalf("%q", r.term.last())
	}
	m, _ := db.GetMemo(r.DB, 1)
	if m == nil || m.Body != "check the cache" || m.Author != "alice" {
		t.Fatalf("%+v", m)
	}
	// the wrong holder, a missing task and a bad no-task
	if out := r.say(SayVerbIn{Requester: "alice", Target: "alice", Body: "x", Task: tid}); out.OK || !strings.HasPrefix(out.Msg, "T1 is with bob, not alice. Try:\n  hl say bob x --task T1") {
		t.Fatalf("%q", out.Msg)
	}
	if out := r.say(SayVerbIn{Requester: "alice", Target: "bob", Body: "x", Task: 9}); out.OK || !strings.HasPrefix(out.Msg, "no task T9. Try:\n  hl say bob x") {
		t.Fatalf("%q", out.Msg)
	}
	no := "because"
	if out := r.say(SayVerbIn{Requester: "alice", Target: "bob", Body: "x", NoTask: &no}); out.OK || !strings.HasPrefix(out.Msg, "--no-task is for replying to the founder") {
		t.Fatalf("%q", out.Msg)
	}
}

func TestAReplyToTheFoundersRequestMustSayWhatItBecomes(t *testing.T) {
	r := newRig(t)
	r.agentUp("alice")
	ask := r.ask("human", "alice", "please build x", "")
	out := r.say(SayVerbIn{Requester: "alice", Target: "human", Body: "on it", Parent: ask})
	if out.OK || !strings.Contains(out.Msg, "#1 is the founder's: say whether work follows it. --task T<n> names the tasks it opened") || !strings.Contains(out.Msg, "Try:\n  hl say human 'on it' --parent 1 --no-task '<why>'") {
		t.Fatalf("%q", out.Msg)
	}
	if row, _ := db.GetRequest(r.DB, ask); row.State != "queued" {
		t.Fatal("answered despite the refusal")
	}
	// exactly one open task of the sender's: the reply attaches to it
	tid := r.newTask("alice", "human", "build x")
	out = r.say(SayVerbIn{Requester: "alice", Target: "human", Body: "on it", Parent: ask})
	if !out.OK || !strings.HasSuffix(out.Msg, "\nattached to T1, your only open task") {
		t.Fatalf("%+v", out)
	}
	_ = tid
	links, _ := db.TaskRequests(r.DB, tid)
	found := false
	for _, l := range links {
		found = found || l.ID == ask
	}
	if !found {
		t.Fatalf("the founder's request is not linked to the task: %+v", links)
	}
	if row, _ := db.GetRequest(r.DB, ask); row.State != "landed" || row.Answer.String != "on it" {
		t.Fatalf("%+v", row)
	}
}

func TestANoTaskReplyRecordsWhy(t *testing.T) {
	r := newRig(t)
	ask := r.ask("human", "alice", "what is 2+2?", "")
	why := "just a question"
	out := r.say(SayVerbIn{Requester: "alice", Target: "human", Body: "4", Parent: ask, NoTask: &why})
	if !out.OK {
		t.Fatalf("%+v", out)
	}
	if n, _, _ := db.LastEventID(r.DB, "alice", "no_task"); n == 0 {
		t.Fatal("no no_task event")
	}
}

func TestCheckHumanResolveGuardsTasksAndTheFoundersRows(t *testing.T) {
	r := newRig(t)
	mine := r.newTask("alice", "alice", "mine")
	theirs := r.newTask("bob", "bob", "theirs")
	row := &db.Request{ID: 5, Requester: db.Human}
	if got := r.checkHumanResolve(r.DB, row, []int64{9}, nil, "alice", false); got != "no task T9" {
		t.Fatal(got)
	}
	if got := r.checkHumanResolve(r.DB, row, []int64{theirs}, nil, "alice", false); got != "T2 is not yours: only a task you hold, or opened, can be named here (it is bob's)" {
		t.Fatal(got)
	}
	db.MarkTaskDone(r.DB, mine, "ok", "reason")
	if got := r.checkHumanResolve(r.DB, row, []int64{mine}, nil, "alice", false); got != "task T1 is already done" {
		t.Fatal(got)
	}
	if got := r.checkHumanResolve(r.DB, row, []int64{theirs}, nil, db.Human, false); got != "" {
		t.Fatalf("the founder's own hands pass: %q", got)
	}
	both := "why"
	if got := r.checkHumanResolve(r.DB, row, []int64{theirs}, &both, "bob", false); !strings.Contains(got, "say whether work follows it. --task T<n> [T<n> ...] names") {
		t.Fatal(got)
	}
	agentRow := &db.Request{ID: 6, Requester: "bob"}
	if got := r.checkHumanResolve(r.DB, agentRow, nil, nil, "alice", false); got != "" {
		t.Fatalf("agent-to-agent rows are conversation: %q", got)
	}
}

func TestPushVerbsCheckTheirTargetsAndHandOver(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	if ok, msg := r.PushVerb("human", 0); ok || msg != "human is not an agent" {
		t.Fatal(msg)
	}
	if ok, msg := r.PushVerb("nobody", 0); ok || msg != "nobody is not an agent" {
		t.Fatal(msg)
	}
	rid := r.ask("alice", "bob", "x", "ask")
	if ok, msg := r.PushVerb("bob", rid); !ok || msg != Handed {
		t.Fatal(msg)
	}
	if !strings.Contains(r.term.last(), "#1 from alice") {
		t.Fatalf("%q", r.term.last())
	}
	task := r.task("bob", "alice", "a task")
	r.term.typed = nil
	if ok, _ := r.PushVerb("bob", task); !ok {
		t.Fatal("task push refused")
	}
	if ok, msg := r.PushAnswerVerb("human"); ok || msg != "human is not an agent" {
		t.Fatal(msg)
	}
	if ok, msg := r.PushAnswerVerb("bob"); !ok || msg != Handed {
		t.Fatal(msg)
	}
}

func TestNotifyCancelledVerbOnlyForADeliveredRequestClosedUnderIt(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	rid := r.ask("alice", "bob", "job", "ask")
	if ok, msg, _ := r.NotifyCancelledVerb(r.DB, rid); ok || msg != "#1 was not cancelled after it was delivered" {
		t.Fatal(msg)
	}
	if ok, msg, _ := r.NotifyCancelledVerb(r.DB, 99); ok || msg != "no request #99" {
		t.Fatal(msg)
	}
	r.TellNow(r.DB, rid)
	after := &After{}
	plan, ok, _, err := r.CancelWrite(r.DB, after, rid, nil, nil)
	r.must(err)
	if !ok {
		t.Fatal("not cancelled")
	}
	r.term.typed = nil
	ok, msg, _ := r.NotifyCancelledVerb(r.DB, rid)
	if !ok || msg != Handed || !strings.Contains(r.term.last(), "#1 from alice was cancelled: withdrawn by the founder") {
		t.Fatalf("%v %q %q", ok, msg, r.term.typed)
	}
	_ = plan
}

func TestShellQuoteMatchesPythons(t *testing.T) {
	for in, want := range map[string]string{"": "''", "plain": "plain", "two words": "'two words'", "it's": `'it'"'"'s'`, "a/b-c_d.e": "a/b-c_d.e", "$x": "'$x'"} {
		if got := shellQuote(in); got != want {
			t.Errorf("%q = %q, want %q", in, got, want)
		}
	}
}

func TestMemoEditAndDeleteAreForTheAuthorOrTheFounder(t *testing.T) {
	r := newRig(t)
	tid := r.newTask("bob", "alice", "job")
	ok, msg, mid, err := r.MemoAdd(r.DB, tid, "alice", "first")
	r.must(err)
	if !ok || msg != "memo 1 added to T1" || mid != 1 {
		t.Fatal(ok, msg)
	}
	if ok, msg, _, _ := r.MemoAdd(r.DB, 9, "alice", "x"); ok || msg != "no task T9" {
		t.Fatal(msg)
	}
	if ok, msg, _, _ := r.MemoAdd(r.DB, tid, "alice", "  "); ok || msg != "a memo needs some text" {
		t.Fatal(msg)
	}
	if ok, msg, _, _ := r.MemoEdit(r.DB, mid, "bob", "mine now"); ok || msg != "memo 1 is alice's: only its author or the founder edits or deletes it" {
		t.Fatal(msg)
	}
	if ok, msg, _, _ := r.MemoEdit(r.DB, mid, "alice", "first"); ok || msg != "memo 1 already reads that way - nothing to edit" {
		t.Fatal(msg)
	}
	ok, msg, got, _ := r.MemoEdit(r.DB, mid, "human", "second")
	if !ok || msg != "memo 1 on T1 edited" || got != tid {
		t.Fatal(ok, msg)
	}
	if m, _ := db.GetMemo(r.DB, mid); m.Body != "second" || !m.EditedAt.Valid {
		t.Fatalf("%+v", m)
	}
	if ok, msg, _, _ := r.MemoDelete(r.DB, 9, "alice"); ok || msg != "no memo 9" {
		t.Fatal(msg)
	}
	if ok, msg, _, _ := r.MemoDelete(r.DB, mid, "alice"); !ok || msg != "memo 1 deleted from T1" {
		t.Fatal(msg)
	}
	if m, _ := db.GetMemo(r.DB, mid); m != nil {
		t.Fatal("memo kept")
	}
}
