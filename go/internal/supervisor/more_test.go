package supervisor

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
)

func TestReviewPingpongStopsAfterThreeRoundsOnTheSameFile(t *testing.T) {
	r := newRig(t)
	if got := reviewPingpong(r.DB, "", "reviewer", "a.go"); got != "" {
		t.Fatal(got)
	}
	var chain int64
	for i := 0; i < 3; i++ {
		id, _ := db.AddRequest(r.DB, db.NewRequest{Requester: "alice", Target: "reviewer", Body: "r", Files: "a.go,b.go", ChainID: itoaChain(chain)})
		if chain == 0 {
			chain = id
			r.DB.Exec("UPDATE requests SET chain_id = ? WHERE id = ?", itoaChain(chain), id)
		}
	}
	got := reviewPingpong(r.DB, itoaChain(chain), "reviewer", "b.go,c.go")
	if got != "review ping-pong: 3 rounds on b.go, c.go in this chain. Two agents who cannot agree is usually a spec question." {
		t.Fatalf("%q", got)
	}
	if got := reviewPingpong(r.DB, itoaChain(chain), "reviewer", "other.go"); got != "" {
		t.Fatalf("another file: %q", got)
	}
}

func itoaChain(n int64) string {
	return strings.TrimSpace(strings.Repeat(" ", 0) + string(rune('0'+n)))
}

func TestTellUndeliveredTypesWhatReachedAnAgentWhileItHadNoSession(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	msg := r.ask("alice", "bob", "while you slept", "ask")
	task := r.task("bob", "alice", "a task")
	r.must(r.TellUndelivered(r.DB, "bob"))
	if got := r.term.last(); !strings.Contains(got, "#"+itoa(msg)+" from alice") {
		t.Fatalf("%q", got)
	}
	for _, typed := range r.term.typed {
		if strings.Contains(typed, "a task") {
			t.Fatalf("a task was told out of its turn: %q (#%d)", typed, task)
		}
	}
	// a closed agent is left to the wake
	db.SetClosed(r.DB, "bob", "")
	r.term.typed = nil
	r.ask("alice", "bob", "another", "ask")
	r.must(r.TellUndelivered(r.DB, "bob"))
	if len(r.term.typed) != 0 {
		t.Fatal("typed at a closed agent")
	}
}

func itoa(n int64) string { return string(rune('0' + n)) }

func TestStartupSettlesOnlyTheSaysNothingTracksAndTypesPendingRows(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	owed := r.ask("alice", "bob", "sent minutes before the restart", "say") // never told
	told := r.ask("alice", "bob", "told by the old daemon", "say")
	db.MarkDelivered(r.DB, told)
	tracked := r.ask("alice", "bob", "written to a session, the ledger holds it", "say")
	batched := r.ask("alice", "bob", "written with another", "say")
	db.MarkDelivered(r.DB, tracked)
	db.MarkDelivered(r.DB, batched)
	for id, op := range map[int64]string{tracked: "request:" + itoa(tracked), batched: "request:" + itoa(batched-1) + "," + itoa(batched) + ",9"} {
		if _, err := r.DB.Exec("INSERT INTO operations(id, kind, state, agent, created_at, updated_at) VALUES(?, 'send', 'sent', 'bob', 1, 1)", "send:bob:"+op); err != nil {
			t.Fatalf("%d: %v", id, err)
		}
	}
	asked := r.ask("alice", "bob", "old ask", "ask")
	db.MarkDelivered(r.DB, asked)
	r.must(r.MigrateOldMessages(r.DB))
	state := func(id int64) string { row, _ := db.GetRequest(r.DB, id); return row.State }
	if state(told) != "landed" || state(owed) != "queued" || state(tracked) != "queued" || state(batched) != "queued" {
		t.Fatalf("told %s owed %s tracked %s batched %s", state(told), state(owed), state(tracked), state(batched))
	}
	// what is owed is told by the tick
	r.must(r.TellUndelivered(r.DB, "bob"))
	if got := r.term.last(); !strings.Contains(got, "sent minutes before the restart") {
		t.Fatalf("an undelivered say was not told after the restart: %q", got)
	}
	if row, _ := db.GetRequest(r.DB, asked); row.State != "queued" || !row.TypedAt.Valid {
		t.Fatalf("an old told ask is treated as typed: %+v", row)
	}
	db.AddPending(r.DB, "bob", "owed since the reboot", 5)
	r.must(r.ReloadPending(r.DB))
	if r.term.last() != "owed since the reboot" {
		t.Fatalf("%q", r.term.last())
	}
}

func TestSweepNoticesRunsTheStoppedAndDigestSweeps(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	db.AddRequest(r.DB, db.NewRequest{Requester: db.NoticeRequester, Target: "bob", Body: "T3 done by alice: ok", Kind: "say", Notice: true})
	r.must(r.SweepNotices(r.clock))
	if !strings.Contains(r.term.last(), "Result digest - 1 result") {
		t.Fatalf("%q", r.term.last())
	}
}

func TestTheTextsOfAReplyAndADismissal(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	q := r.ask("alice", "bob", "first line of the question\nsecond", "ask")
	out, _, _ := r.Say(r.DB, SayIn{Target: "alice", Body: "the answer", Requester: "bob", ParentID: q})
	head := mustReq(r, out.RID)
	if got := inReplyTo(r.DB, head); got != "in reply to #"+itoa(q)+": first line of the question" {
		t.Fatalf("%q", got)
	}
	if got := tellText(r.DB, head, 1, nil, false, nil, ""); !strings.HasPrefix(got, "#2 from bob (in reply to #1: first line of the question), waiting for you:\nthe answer\n") {
		t.Fatalf("%q", got)
	}
	// parked and send-now tails
	other := mustReq(r, r.ask("alice", "bob", "x", "ask"))
	if got := tellText(r.DB, other, 2, head, true, nil, ""); !strings.Contains(got, "[Queued behind #2, which is still open: the next task comes when you hand it on or finish it.]") {
		t.Fatalf("%q", got)
	}
	if got := tellText(r.DB, other, 1, head, false, nil, ""); !strings.Contains(got, "[Sent now, ahead of #2, which is still open.]") {
		t.Fatalf("%q", got)
	}
	if MoreWaiting("ask", 2) != "2 more waiting - the next comes when you answer this one." || MoreWaiting("say", 3) != "" || MoreWaiting("task", 0) != "" {
		t.Fatal("more waiting")
	}
	// a dismissal reads as a state label
	card := r.ask("alice", "human", "pick", "")
	db.CloseIfQueued(r.DB, card, "done", "", db.CloseOpts{DismissedBy: "human", DismissedReason: "later"})
	row, _ := db.GetRequest(r.DB, card)
	if line, _ := AnswerLine(row); !strings.Contains(line, "human: dismissed by human - later") {
		t.Fatalf("%q", line)
	}
}

func mustReq(r *rig, id int64) *db.Request {
	row, err := db.GetRequest(r.DB, id)
	r.must(err)
	return row
}

func TestWriteAtomicLeavesNoTempFileAndReplaces(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "x.json")
	for _, text := range []string{"one", "two"} {
		if err := writeAtomic(p, text); err != nil {
			t.Fatal(err)
		}
	}
	if b, _ := os.ReadFile(p); string(b) != "two" {
		t.Fatalf("%q", b)
	}
	entries, _ := os.ReadDir(dir)
	if len(entries) != 1 {
		t.Fatalf("%v", entries)
	}
	if err := writeAtomic(filepath.Join(dir, "nowhere", "x"), "t"); err == nil {
		t.Fatal("wrote into a missing directory")
	}
}

func TestTheImagesFollowTheBodyAfterTheCut(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	head := mustReq(r, r.ask("alice", "bob", strings.Repeat("w", 500), "say"))
	lines := "[image 1: /h/attachments/a.png (image/png, 3 KB)]\n[image 2: /h/attachments/b.jpg (image/jpeg, 9 KB)]"
	got := tellText(r.DB, head, 1, nil, false, nil, lines)
	if !strings.Contains(got, strings.Repeat("w", 400)+"…\n"+lines+"\n") {
		t.Fatalf("%q", got)
	}
	if got := tellText(r.DB, head, 1, nil, false, nil, ""); strings.Contains(got, "[image") {
		t.Fatalf("%q", got)
	}
	empty := mustReq(r, r.ask("alice", "bob", "", "say"))
	if got := tellText(r.DB, empty, 1, nil, false, nil, lines); strings.Contains(got, "(empty)") || !strings.Contains(got, "waiting for you:\n"+lines) {
		t.Fatalf("%q", got)
	}
}
