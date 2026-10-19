package supervisor

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

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
	if !out.OK || r.ids(out.Msg) != "queued as #1 for bob" {
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
	// bob reports to alice, the member its team is named after; written before the table is first read and sealed
	os.WriteFile(filepath.Join(r.home, "team.yaml"), []byte(strings.Replace(teamYAML, "eng: [alice, bob]", "alice: [alice, bob]", 1)), 0o644)
	out := r.say(SayVerbIn{Requester: "alice", Target: "bob", Body: "fix the bug"})
	want := "work cannot reach a report by message: make it a task (`hl task new`), or say --task T<n> to memo one. Try:\n  hl task new --assign bob -t 2h '<title>' 'fix the bug'"
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
	if out.OK || !strings.Contains(r.ids(out.Msg), "#1 is the founder's: say whether work follows it. --task T<n> names the tasks it opened") || !strings.Contains(r.ids(out.Msg), "Try:\n  hl say human 'on it' --parent #1 --no-task '<why>'") {
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
	if row, _ := db.GetRequest(r.DB, ask); row.State != "queued" {
		t.Fatalf("a reply links the founder's request to the task and closes nothing: %+v", row)
	}
}

func TestAReplyWithATaskLandsInItsThreadNotAsAMemo(t *testing.T) {
	r := newRig(t)
	r.agentUp("alice")
	said := r.say(SayVerbIn{Requester: "human", Target: "alice", Body: "why a memo?"}) // the founder in chat
	if !said.OK {
		t.Fatalf("%+v", said)
	}
	r.DB.Exec("UPDATE requests SET state = 'landed' WHERE id = 1") // read, as a chat message is
	tid := r.newTask("human", "bob", "decide")                     // the founder's, opened by someone else
	out := r.say(SayVerbIn{Requester: "alice", Target: "human", Body: "here is why", Parent: 1, Task: tid})
	if !out.OK || strings.Contains(out.Msg, "memo") {
		t.Fatalf("%+v", out)
	}
	if m, _ := db.GetMemo(r.DB, 1); m != nil {
		t.Fatalf("the reply became a memo: %+v", m)
	}
	var rid int64
	r.DB.QueryRow("SELECT id FROM requests WHERE requester = 'alice' AND target = 'human' AND parent_id = 1").Scan(&rid)
	if rid == 0 {
		t.Fatal("no reply in the thread of #1")
	}
	links, _ := db.TaskRequests(r.DB, tid)
	found := false
	for _, l := range links {
		found = found || l.ID == rid
	}
	if !found {
		t.Fatalf("the reply #%d is not linked to T%d: %+v", rid, tid, links)
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
}

func TestNotifyCancelledVerbOnlyForADeliveredRequestClosedUnderIt(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	rid := r.ask("alice", "bob", "job", "ask")
	if ok, msg, _ := r.NotifyCancelledVerb(r.DB, rid); ok || r.ids(msg) != "#1 was not cancelled after it was delivered" {
		t.Fatal(msg)
	}
	if ok, msg, _ := r.NotifyCancelledVerb(r.DB, 99); ok || msg != "no such message" {
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

func TestASayToTheFounderThatNamesAMessageIsRefusedAndAsksForWords(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	r.say(SayVerbIn{Requester: "alice", Target: "bob", Body: "first"})
	var ref string
	r.DB.QueryRow("SELECT ref FROM requests WHERE id = 1").Scan(&ref)
	for _, body := range []string{"done, see msg_0123456", "as in " + ref[:8] + " earlier", "re " + strings.ToUpper(ref)} {
		out := r.say(SayVerbIn{Requester: "alice", Target: db.Human, Body: body, NoTask: ptr("chat")})
		if out.OK || !strings.Contains(out.Msg, "say in words what that message was about") {
			t.Fatalf("%q: %+v", body, out)
		}
	}
	for _, body := range []string{"merged as 0123456789abcdef0123456789abcdef01234567", "PR #3 is up", "all green"} {
		if out := r.say(SayVerbIn{Requester: "alice", Target: db.Human, Body: body, NoTask: ptr("chat")}); strings.Contains(out.Msg, "names a message") {
			t.Fatalf("%q is no message id: %+v", body, out)
		}
	}
}

// The daemon closes the database, and a test its home, only once a telling started off the caller has ended.
func TestWaitReturnsOnceTheTellingsOffTheCallerHaveEnded(t *testing.T) {
	RunInline = false
	t.Cleanup(func() { RunInline = true })
	e := &Env{}
	release, ended := make(chan struct{}), make(chan bool, 1)
	e.background("a telling", func() {
		<-release
		ended <- true
	})
	waited := make(chan struct{})
	go func() {
		e.Wait()
		close(waited)
	}()
	select {
	case <-waited:
		t.Fatal("Wait returned while the telling still ran")
	case <-time.After(50 * time.Millisecond):
	}
	close(release)
	<-waited
	if len(ended) != 1 {
		t.Fatal("Wait returned before the telling ended")
	}
}
