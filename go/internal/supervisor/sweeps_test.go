package supervisor

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
)

func TestSweepHoldsRecordsWhyEachTaskWaitsAndWarnsOnceWhenNothingIsInTheWay(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	a := r.newTask("bob", "alice", "first")
	b := r.newTask("bob", "alice", "second")
	db.SetCurrentTask(r.DB, "bob", a, true)
	r.must(r.SweepHolds(r.clock))
	raw, _, _ := db.Meta(r.DB, "taskhold:1")
	if raw != `{"why": "nothing is in the way - the daemon sends it on its next tick", "able": true, "since": 100000, "able_since": 100000}` {
		t.Fatalf("%s", raw)
	}
	raw, _, _ = db.Meta(r.DB, "taskhold:2")
	if raw != `{"why": "behind T1, which bob holds (still open)", "able": false, "since": 100000}` {
		t.Fatalf("%s", raw)
	}
	// blocked
	c := r.newTask("alice", "alice", "dep")
	db.AddBlock(r.DB, b, c)
	r.clock += 10
	r.must(r.SweepHolds(r.clock))
	raw, _, _ = db.Meta(r.DB, "taskhold:2")
	if raw != `{"why": "blocked by T3 (alice)", "able": false, "since": 100010}` {
		t.Fatalf("%s", raw)
	}
	// able for five minutes: the founder is told once
	r.clock += AbleWarnS
	r.must(r.SweepHolds(r.clock))
	r.must(r.SweepHolds(r.clock + 1))
	var warned int
	r.DB.QueryRow("SELECT COUNT(*) FROM events WHERE kind = 'report'").Scan(&warned)
	if warned != 2 || !r.logged("T1: waited 5 min for an owner that looks able - founder told") { // T1 and T3 are both able
		t.Fatalf("%d %v", warned, r.logs)
	}
	var body string
	r.DB.QueryRow("SELECT body FROM requests WHERE target = 'human' AND kind = 'say' AND requester = 'bob'").Scan(&body)
	if !strings.HasPrefix(body, "T1 has waited 5 min for bob, which looks able to take it (nothing is in the way") {
		t.Fatalf("%q", body)
	}
	// a finished task's line goes
	db.MarkTaskDone(r.DB, a, "ok", "reason")
	r.must(r.SweepHolds(r.clock + 2))
	if _, found, _ := db.Meta(r.DB, "taskhold:1"); found {
		t.Fatal("a done task still has a line")
	}
}

func TestAgentReasonNamesTheFirstGateInTheOrderItIsMet(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	if why, able := r.agentReason(r.DB, "bob", r.clock); why != "" || !able {
		t.Fatalf("%q %v", why, able)
	}
	r.NoteHold("bob", "its screen is not idle (mid-turn, or unreadable)", true)
	if why, able := r.agentReason(r.DB, "bob", r.clock); why != "its screen is not idle (mid-turn, or unreadable)" || !able {
		t.Fatalf("%q %v", why, able)
	}
	db.SetAgentState(r.DB, "bob", "RUNNING", 0, "")
	if _, able := r.agentReason(r.DB, "bob", r.clock); able {
		t.Fatal("a contestable screen reading is contested only when the state says idle")
	}
	db.SetCursor(r.DB, "clear_owed:bob", 5)
	if why, _ := r.agentReason(r.DB, "bob", r.clock); !strings.Contains(why, "owes a context clear first") {
		t.Fatal(why)
	}
	r.Stuck = fakeStuck{why: "bob is mid-turn - nothing is typed at a working agent"}
	if why, _ := r.agentReason(r.DB, "bob", r.clock); !strings.Contains(why, "mid-turn") {
		t.Fatal(why)
	}
	db.BlockOpen(r.DB, "bob", "dialog", "trust?", "w", 1, 1, "tick", "t")
	if why, _ := r.agentReason(r.DB, "bob", r.clock); why != "dialog: trust?" {
		t.Fatal(why)
	}
	r.NoteFailure("bob", "the wake failed")
	if why, _ := r.agentReason(r.DB, "bob", r.clock); why != "the wake failed" {
		t.Fatal(why)
	}
	if why, _ := r.agentReason(r.DB, "bob", r.clock+HeldFreshS+1); why == "the wake failed" {
		t.Fatal("a stale failure is still told")
	}
}

func TestDigestFoldsNoticesAndTypesOneMessageToAnIdleAgent(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	for _, body := range []string{"T5 done by alice: it works", "T6 discarded by alice: not needed", "something else"} {
		db.AddRequest(r.DB, db.NewRequest{Requester: db.NoticeRequester, Target: "bob", Body: body, Kind: "say", Notice: true})
	}
	db.SetAgentState(r.DB, "bob", "RUNNING", 0, "")
	r.must(r.SweepDigests(r.clock + 10))
	if len(r.term.typed) != 0 {
		t.Fatal("a notice woke a working agent before its window ran out")
	}
	r.must(r.SweepDigests(r.clock + 900))
	want := "Result digest - 3 results since "
	got := r.term.last()
	if !strings.Contains(got, want) || !strings.Contains(got, "T5 · alice · it works (#1) T6 · alice · discarded: not needed (#2) something else (#3)") {
		t.Fatalf("%q", got)
	}
	if left, _ := db.PendingNotices(r.DB, "bob"); len(left) != 0 {
		t.Fatal("digested notices stay pending")
	}
}

func TestDigestIsTypedAtOnceToAnIdleSession(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	db.AddRequest(r.DB, db.NewRequest{Requester: db.NoticeRequester, Target: "bob", Body: "T9 done by alice: ok", Kind: "say", Notice: true})
	r.must(r.SweepDigests(r.clock))
	if !strings.Contains(r.term.last(), "Result digest - 1 result since ") {
		t.Fatalf("%q", r.term.last())
	}
}

func TestDigestTextCapsItsLines(t *testing.T) {
	var rows []*db.Request
	for i := 0; i < 23; i++ {
		rows = append(rows, &db.Request{ID: int64(i + 1), Body: "T1 done by a: " + strings.Repeat("x", 100)})
		rows[i].LandedAt.Valid, rows[i].LandedAt.Int64 = true, 60
	}
	text := DigestText(rows)
	lines := strings.Split(text, "\n")
	if len(lines) != 1+20+1 || lines[21] != "(+3 more: `hl inbox --all`)" {
		t.Fatalf("%d %q", len(lines), lines[21])
	}
	if !strings.HasSuffix(strings.Split(lines[1], " · ")[2], "… (#1)") {
		t.Fatalf("%q", lines[1])
	}
}

func TestStoppedAgentsAreToldToWhoeverWaitsOnThemOncePerSpell(t *testing.T) {
	r := newRig(t)
	r.agentUp("alice")
	r.agentUp("bob")
	r.newTask("bob", "alice", "bob's job")
	db.SetAgentState(r.DB, "bob", "STALLED", 0, "cannot get past a dialog: Trust?")
	r.must(r.SweepStopped(r.clock))
	if got := r.term.last(); !strings.Contains(got, "bob is stalled: cannot get past a dialog: Trust?") {
		t.Fatalf("%q", got)
	}
	r.term.typed = nil
	r.must(r.SweepStopped(r.clock + 1))
	if len(r.term.typed) != 0 {
		t.Fatal("told twice in one spell")
	}
	db.SetAgentState(r.DB, "bob", "IDLE", 0, "")
	r.must(r.SweepStopped(r.clock + 2))
	db.SetAgentState(r.DB, "bob", "STALLED", 0, "gone wrong")
	r.must(r.SweepStopped(r.clock + 3))
	if got := r.term.last(); !strings.Contains(got, "bob is FATAL: gone wrong") {
		t.Fatalf("a new spell is told afresh: %q", got)
	}
}

func TestLandingSweepConfirmsFindsAndRetypes(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	db.SetAccount(r.DB, "bob", "acct", "recorded")
	tr := filepath.Join(r.home, "s1.jsonl")
	r.sessions.transcripts = map[string]string{"s1": tr}
	os.WriteFile(tr, nil, 0o644)
	rid := r.ask("alice", "bob", "typed but unseen", "ask")
	r.TellNow(r.DB, rid)
	// the transcript does not show it yet and it was typed a moment ago: wait
	r.clock += 10
	r.must(r.LandingSweep(r.DB, "bob"))
	if row, _ := db.GetRequest(r.DB, rid); row.State != "queued" || !row.TypedAt.Valid {
		t.Fatalf("%+v", row)
	}
	// past the wait it is owed again
	r.clock += LandingWaitS
	r.must(r.LandingSweep(r.DB, "bob"))
	row, _ := db.GetRequest(r.DB, rid)
	if row.TypedAt.Valid || row.DeliveredAt.Valid {
		t.Fatalf("not owed again: %+v", row)
	}
	if !r.logged("#1 never reached its transcript - typed again") {
		t.Fatal(r.logs)
	}
	// it lands
	r.TellNow(r.DB, rid)
	line := `{"type":"queue-operation","operation":"enqueue","content":"#1 from alice, waiting for you:\ntyped but unseen"}` + "\n"
	os.WriteFile(tr, []byte(line), 0o644)
	r.must(r.LandingSweep(r.DB, "bob"))
	if row, _ := db.GetRequest(r.DB, rid); row.State != "landed" || !row.LandedAt.Valid {
		t.Fatalf("%+v", row)
	}
}

func TestLandingTakesATypedLineAsLandedWhereTheVendorKeepsNoTranscript(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	db.SetAccount(r.DB, "bob", "acct", "recorded")
	rid := r.ask("alice", "bob", "x", "ask")
	r.TellNow(r.DB, rid)
	r.must(r.LandingSweep(r.DB, "bob"))
	if row, _ := db.GetRequest(r.DB, rid); row.State != "landed" {
		t.Fatalf("%+v", row)
	}
}

func TestLandingStateNamesWhereARequestIs(t *testing.T) {
	var row db.Request
	if LandingState(&row) != "queued" {
		t.Fatal()
	}
	row.TypedAt.Valid, row.TypedAt.Int64 = true, 5
	if LandingState(&row) != "typed" {
		t.Fatal()
	}
	row.LandedAt.Valid, row.LandedAt.Int64 = true, 6
	if LandingState(&row) != "delivered" {
		t.Fatal()
	}
}
