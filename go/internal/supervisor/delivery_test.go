package supervisor

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
)

func (r *rig) ask(from, to, body, kind string) int64 {
	r.t.Helper()
	out, err := r.Ask(r.DB, AskIn{Target: to, Body: body, Requester: from, Kind: kind})
	r.must(err)
	if !out.OK {
		r.t.Fatalf("ask refused: %s", out.Message)
	}
	out.Finish()
	return out.RID
}

func TestTellNowTypesAMessageAndSettlesASay(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	rid := r.ask("alice", "bob", "psst", "say")
	got, err := r.TellNow(r.DB, rid)
	r.must(err)
	if got != "typed" {
		t.Fatal(got)
	}
	want := "#1 from alice, waiting for you:\npsst\n`hl read 1` shows it whole; it asks nothing."
	if r.term.last() != want {
		t.Fatalf("%q", r.term.last())
	}
	row, _ := db.GetRequest(r.DB, rid)
	if row.State != "landed" || !row.DeliveredAt.Valid || !row.TypedAt.Valid {
		t.Fatalf("a typed say is settled quietly: %+v", row)
	}
	if again, _ := r.TellNow(r.DB, rid); again != "no longer queued" {
		t.Fatal(again)
	}
	if len(r.channel.markers) != 1 || r.channel.markers[0] != "bob:daemon_typed" {
		t.Fatalf("%v", r.channel.markers)
	}
}

func TestTellNowHoldsForABlockerTheWallAndAClosedSession(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	rid := r.ask("alice", "bob", "do this", "ask")
	r.term.blocker = &blockerOf
	got, _ := r.TellNow(r.DB, rid)
	if !strings.HasPrefix(got, "queued - ") || len(r.term.typed) != 0 {
		t.Fatalf("%q typed %v", got, r.term.typed)
	}
	if row, _ := db.GetRequest(r.DB, rid); row.DeliveredAt.Valid {
		t.Fatal("marked delivered without being typed")
	}
	r.term.blocker = nil
	r.Waits = fakeWaits{limit: map[string]bool{"bob": true}}
	if got, _ := r.TellNow(r.DB, rid); got != "queued - bob is at its usage limit" {
		t.Fatal(got)
	}
	r.Waits = fakeWaits{limit: map[string]bool{}}
	r.term.sendFails = true
	if got, _ := r.TellNow(r.DB, rid); got != "queued - pane gone" {
		t.Fatal(got)
	}
	if row, _ := db.GetRequest(r.DB, rid); row.DeliveredAt.Valid {
		t.Fatal("a failed send leaves the request marked delivered")
	}
	r.term.sendFails = false
	// a closed session is woken first
	db.SetClosed(r.DB, "bob", "old")
	if got, _ := r.TellNow(r.DB, rid); got != "typed" || len(r.life.woke) != 1 {
		t.Fatalf("%q woke %v", got, r.life.woke)
	}
	if !strings.Contains(r.term.last(), "answer with `hl say alice --parent 1") {
		t.Fatalf("%q", r.term.last())
	}
}

func TestTellNowFindsALineAlreadyInTheTranscript(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	db.SetAccount(r.DB, "bob", "acct", "recorded")
	tr := filepath.Join(r.home, "s1.jsonl")
	r.sessions.transcripts = map[string]string{"s1": tr}
	rid := r.ask("alice", "bob", "already there", "ask")
	line := `{"type":"user","message":{"content":"#1 from alice, waiting for you:\nalready there"}}` + "\n"
	r.must(os.WriteFile(tr, []byte(line), 0o644))
	got, _ := r.TellNow(r.DB, rid)
	if got != "already in its transcript" || len(r.term.typed) != 0 {
		t.Fatalf("%q typed %v", got, r.term.typed)
	}
	if row, _ := db.GetRequest(r.DB, rid); row.State != "landed" || !row.LandedAt.Valid {
		t.Fatalf("%+v", row)
	}
}

func TestSendMessageKeepsThePendingRowUntilItIsTyped(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	r.term.blocker = &blockerOf
	ok, msg, err := r.SendMessage(r.DB, "bob", "hello there")
	r.must(err)
	if !ok || !strings.HasPrefix(msg, "bob: queued - ") {
		t.Fatal(msg)
	}
	if rows, _ := db.ListPending(r.DB, "bob"); len(rows) != 1 {
		t.Fatal("the pending row is the thing a reboot must not lose")
	}
	r.term.blocker = nil
	sent, err := r.DeliverPending(r.DB, "bob")
	r.must(err)
	if !sent || r.term.last() != "hello there" {
		t.Fatalf("%v %q", sent, r.term.last())
	}
	if rows, _ := db.ListPending(r.DB, "bob"); len(rows) != 0 {
		t.Fatal("typed and kept")
	}
	// no session: woken, then typed
	r.Observer = fakeObserver{map[string]bool{}}
	r.Terminal = noSession{r.term}
	r.term.typed = nil
	ok, msg, _ = r.SendMessage(r.DB, "bob", "again")
	if !ok || !strings.HasPrefix(msg, "bob: queued - woken, will type once free") || len(r.life.woke) != 1 {
		t.Fatalf("%s %v", msg, r.life.woke)
	}
}

func TestDeliverQueuedTellsTheHeadTaskThenBatchesWhatArrivesBehindIt(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	head := r.task("bob", "alice", "first job")
	sent, err := r.DeliverQueued(r.DB, "bob")
	r.must(err)
	if !sent {
		t.Fatal("not delivered")
	}
	if n, _ := db.Cursor(r.DB, "notify:bob"); n != head {
		t.Fatalf("notify cursor %d", n)
	}
	if got := r.term.last(); !strings.HasPrefix(got, "#1 from alice, waiting for you:\nfirst job\n`hl read 1` shows it whole; you own T1 now: hand it on with ") {
		t.Fatalf("%q", got)
	}
	// two more arrive behind the open head: told as a batch, once a minute
	r.task("bob", "alice", "second job")
	r.task("bob", "alice", "third job")
	r.term.typed = nil
	if sent, _ := r.DeliverQueued(r.DB, "bob"); sent {
		t.Fatal("a batch inside the minute")
	}
	r.clock += BatchEveryS
	sent, _ = r.DeliverQueued(r.DB, "bob")
	if !sent || !strings.HasPrefix(r.term.last(), "2 more tasks queued behind #1, which is still open:\n#2 from alice: second job #3 from alice: third job\n") {
		t.Fatalf("%v %q", sent, r.term.last())
	}
	if sent, _ := r.DeliverQueued(r.DB, "bob"); sent {
		t.Fatal("told twice")
	}
}

func TestSeveralUntoldTasksGoOutAsOneBatchWithTheHeadFirst(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	r.task("bob", "alice", "one")
	r.task("bob", "alice", "two")
	sent, _ := r.DeliverQueued(r.DB, "bob")
	if !sent || !strings.HasPrefix(r.term.last(), "2 tasks waiting for you:\n#1 from alice: one #2 from alice: two\n`hl read <id>` shows one whole.") {
		t.Fatalf("%v %q", sent, r.term.last())
	}
	if n, _ := db.Cursor(r.DB, "notify:bob"); n != 1 {
		t.Fatal(n)
	}
}

func TestDeliverQueuedExplainsWhyItIsHolding(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	r.task("bob", "alice", "job")
	r.term.typeable = false
	if sent, _ := r.DeliverQueued(r.DB, "bob"); sent {
		t.Fatal("typed at a working agent")
	}
	why, contestable, ok := r.HeldReason("bob", r.clock)
	if !ok || why != "its screen is not idle (mid-turn, or unreadable)" || !contestable {
		t.Fatalf("%q %v %v", why, contestable, ok)
	}
	if !r.logged("#1 is held - its screen is not idle") {
		t.Fatal(r.logs)
	}
	r.logs = nil
	r.DeliverQueued(r.DB, "bob")
	if r.logged("is held") {
		t.Fatal("the same reason is logged on every tick")
	}
	r.term.typeable = true
	r.Observer = fakeObserver{map[string]bool{}}
	r.term.typed = nil
	// table says no session and the screen draws none
	r.Terminal = noSession{r.term}
	if sent, _ := r.DeliverQueued(r.DB, "bob"); sent || len(r.term.typed) != 0 {
		t.Fatal("typed at a shell prompt")
	}
	if why, _, _ := r.HeldReason("bob", r.clock); why != "no session is running in its terminal" {
		t.Fatal(why)
	}
}

type noSession struct{ *fakeTerm }

func (noSession) SessionOnScreen(string) (bool, bool) { return false, true }

func TestEnforceEdgesDeniesWhatTheTableDoesNotAllow(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	rid := r.ask("alice", "bob", "x", "")
	// the row says a different relation than the table
	r.DB.Exec("UPDATE requests SET edge_ok = NULL, relation = 'supervisor' WHERE id = ?", rid)
	ok, err := r.EnforceEdges(r.DB, "bob")
	r.must(err)
	if !ok {
		t.Fatal("untrusted")
	}
	row, _ := db.GetRequest(r.DB, rid)
	if row.State != "denied" || !strings.HasPrefix(row.Answer.String, "refused at delivery: the routing table says peer/async/None, the request says supervisor/async/None") {
		t.Fatalf("%+v", row)
	}
	// a system name is not an agent's edge
	sys, _ := db.AddRequest(r.DB, db.NewRequest{Requester: db.NoticeRequester, Target: "bob", Body: "s", Kind: "say"})
	r.DB.Exec("UPDATE requests SET edge_ok = NULL WHERE id = ?", sys)
	r.EnforceEdges(r.DB, "bob")
	if row, _ := db.GetRequest(r.DB, sys); !row.EdgeOK.Valid || row.State != "queued" {
		t.Fatalf("%+v", row)
	}
	// a changed team.yaml holds everything
	os.WriteFile(filepath.Join(r.home, "team.yaml"), []byte(teamYAML+"# an agent wrote this\n"), 0o644)
	if ok, _ := r.EnforceEdges(r.DB, "bob"); ok {
		t.Fatal("trusted a changed table")
	}
	if !r.logged("nothing delivered - team.yaml is not trusted") {
		t.Fatal(r.logs)
	}
}

func TestDeliverAnswersTellsWhatCameBackOnce(t *testing.T) {
	r := newRig(t)
	r.agentUp("alice")
	q := r.ask("alice", "bob", "what time is it", "ask")
	ok, msg, err := r.Resolve(r.DB, ResolveIn{RID: q, Verdict: "noon", Notify: true})
	r.must(err)
	if !ok || !strings.Contains(msg, "alice has been told") {
		t.Fatal(ok, msg)
	}
	if r.term.last() != "Answers to what you asked:\n#1 (you asked: what time is it) bob: noon" {
		t.Fatalf("%q", r.term.last())
	}
	if again, _ := r.DeliverAnswers(r.DB, "alice"); again {
		t.Fatal("told twice")
	}
}

func TestWaitingPromptIsTheTellTheNextTickWouldType(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	rid := r.ask("alice", "bob", "wake up", "ask")
	text, told, ok, err := r.WaitingPrompt(r.DB, "bob")
	r.must(err)
	if !ok || !strings.HasPrefix(text, "#1 from alice, waiting for you:\nwake up\n") {
		t.Fatalf("%q", text)
	}
	if row, _ := db.GetRequest(r.DB, rid); !row.DeliveredAt.Valid {
		t.Fatal("not marked delivered before the launch")
	}
	told("s7")
	if n, _ := db.Cursor(r.DB, "notify:bob"); n != rid {
		t.Fatal(n)
	}
	if s, _ := db.NotifiedSession(r.DB, "bob"); s != "s7" {
		t.Fatal(s)
	}
	r.must(db.MarkLanded(r.DB, rid)) // the resumed session read it: it leaves the line
	db.AddPending(r.DB, "bob", "a bare line", 5)
	text, told, ok, _ = r.WaitingPrompt(r.DB, "bob")
	if !ok || text != "a bare line" {
		t.Fatal(text)
	}
	told("s7")
	if rows, _ := db.ListPending(r.DB, "bob"); len(rows) != 0 {
		t.Fatal("pending row kept")
	}
}

// task opens a queued task for owner with the delivery request, as task new does.
func (r *rig) task(owner, opener, title string) int64 {
	r.t.Helper()
	tid, err := db.NewTask(r.DB, title, owner, opener, 0, db.DefaultPriority, "", 0)
	r.must(err)
	db.SetTaskQueued(r.DB, tid, opener)
	rid := r.ask(opener, owner, title, "task")
	r.must(db.LinkRequest(r.DB, tid, rid))
	return rid
}
