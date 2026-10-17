package supervisor

import (
	"fmt"
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
	if raw != `{"why": "nothing is in the way - the daemon sends it on its next tick", "on": "owner", "able": true, "since": 100000, "able_since": 100000}` {
		t.Fatalf("%s", raw)
	}
	raw, _, _ = db.Meta(r.DB, "taskhold:2")
	if raw != `{"why": "behind T1, which bob holds (still open)", "on": "owner", "able": false, "since": 100000}` {
		t.Fatalf("%s", raw)
	}
	// blocked
	c := r.newTask("alice", "alice", "dep")
	db.AddBlock(r.DB, b, c)
	r.clock += 10
	r.must(r.SweepHolds(r.clock))
	raw, _, _ = db.Meta(r.DB, "taskhold:2")
	if raw != `{"why": "blocked by T3 (alice)", "on": "task", "able": false, "since": 100010}` {
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

// Only a task waiting for delivery to an able owner is a missed delivery; a task that waits on purpose never raises the alarm.
func TestTheMissedDeliveryAlarmSkipsTasksThatWaitOnPurpose(t *testing.T) {
	exec := func(r *rig, query string, args ...any) {
		_, err := r.DB.Exec(query, args...)
		r.must(err)
	}
	for _, c := range []struct {
		name  string
		wait  func(r *rig, tid int64)
		alarm bool
	}{
		{"waiting for delivery to an able owner", func(*rig, int64) {}, true},
		{"reported, awaiting the founder's accept", func(r *rig, tid int64) {
			exec(r, "UPDATE tasks SET reporter = 'human', reported_at = ?, report = 'the doc' WHERE id = ?", r.clock, tid)
		}, false},
		{"blocked by a task", func(r *rig, tid int64) { db.AddBlock(r.DB, tid, r.newTask("alice", "alice", "dep")) }, false},
		{"waiting on its open child", func(r *rig, tid int64) {
			_, err := db.NewTask(r.DB, "child", "alice", "bob", tid, db.DefaultPriority, "", 0, "")
			r.must(err)
		}, false},
		{"paused, waiting on a human", func(r *rig, tid int64) {
			db.AddBlock(r.DB, tid, r.newTask(db.Human, "bob", "decide"))
			db.AdvanceTaskTimeout(r.DB, tid, r.clock, 600)
			db.PauseTaskTimeout(r.DB, tid, r.clock)
		}, false},
		{"a draft", func(r *rig, tid int64) {
			exec(r, "UPDATE tasks SET queued_by = NULL, queued_at = NULL WHERE id = ?", tid)
		}, false},
		{"owned by the founder", func(r *rig, tid int64) { exec(r, "UPDATE tasks SET owner = 'human' WHERE id = ?", tid) }, false},
	} {
		t.Run(c.name, func(t *testing.T) {
			r := newRig(t)
			r.agentUp("bob")
			tid := r.newTask("bob", "alice", "job")
			c.wait(r, tid)
			r.must(r.SweepHolds(r.clock))
			r.must(r.SweepHolds(r.clock + AbleWarnS))
			var warned int
			r.DB.QueryRow("SELECT COUNT(*) FROM requests WHERE target = 'human' AND body LIKE ?", fmt.Sprintf("T%d has waited %%", tid)).Scan(&warned)
			if (warned == 1) != c.alarm || warned > 1 {
				t.Fatalf("alarm %v, %d told", c.alarm, warned)
			}
		})
	}
}

func TestAWaitOnTheOwnerIsFiledUnderItsAccountsWallOrCap(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	if c := r.ownerCause(r.DB, "bob"); c != "owner" {
		t.Fatalf("nothing holds the account: %q", c)
	}
	r.life.wait = "cap"
	if c := r.ownerCause(r.DB, "bob"); c != "cap" {
		t.Fatalf("no free slot: %q", c)
	}
	r.Waits = fakeWaits{limit: map[string]bool{"bob": true}}
	if c := r.ownerCause(r.DB, "bob"); c != "wall" {
		t.Fatalf("at its usage limit: %q", c)
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

func TestABusyAgentPastTheWindowGetsNoDigestUntilItsTurnEndsThenOne(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	for _, body := range []string{"T5 done by alice: it works", "T6 discarded by alice: not needed", "something else"} {
		db.AddRequest(r.DB, db.NewRequest{Requester: db.NoticeRequester, Target: "bob", Body: body, Kind: "say", Notice: true})
	}
	db.SetAgentState(r.DB, "bob", "RUNNING", 0, "")
	for _, at := range []int64{10, 900, 5000} {
		r.must(r.SweepDigests(r.clock + at))
		if len(r.term.typed) != 0 {
			t.Fatalf("typed to a working agent %ds after its first notice", at)
		}
	}
	db.SetAgentState(r.DB, "bob", "IDLE", 0, "") // the turn ended
	r.must(r.SweepDigests(r.clock + 5030))
	r.must(r.SweepDigests(r.clock + 5060))
	if len(r.term.typed) != 1 {
		t.Fatalf("one digest at the turn's end: %q", r.term.typed)
	}
	want := "Result digest - 3 results since "
	got := r.term.last()
	if !strings.Contains(got, want) || !strings.Contains(got, "shows one whole:\nT5 · alice · it works (#1)\nT6 · alice · discarded: not needed (#2)\nsomething else (#3)\n") {
		t.Fatalf("%q", got)
	}
	if left, _ := db.PendingNotices(r.DB, "bob"); len(left) != 0 {
		t.Fatal("digested notices stay pending")
	}
}

func TestAnIdleAgentGetsItsDigestAtTheWindowNotBefore(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	db.AddRequest(r.DB, db.NewRequest{Requester: db.NoticeRequester, Target: "bob", Body: "T9 done by alice: ok", Kind: "say", Notice: true})
	r.must(r.SweepDigests(r.clock + 899))
	if len(r.term.typed) != 0 {
		t.Fatalf("typed before the window ran: %q", r.term.typed)
	}
	r.must(r.SweepDigests(r.clock + 900))
	if !strings.Contains(r.term.last(), "Result digest - 1 result since ") {
		t.Fatalf("%q", r.term.last())
	}
}

func TestAnAgentAtItsPromptWithBackgroundWorkStillGetsItsDigest(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	r.must(db.SetCursor(r.DB, "bgwork:bob", 1))
	db.AddRequest(r.DB, db.NewRequest{Requester: db.NoticeRequester, Target: "bob", Body: "T9 done by alice: ok", Kind: "say", Notice: true})
	r.must(r.SweepDigests(r.clock + 900))
	if !strings.Contains(r.term.last(), "Result digest - 1 result since ") {
		t.Fatalf("the digest waited on background work: %q", r.term.typed)
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

func TestAHeldMemoWaitsForTheSameWindowAsAnyNotice(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	db.AddRequest(r.DB, db.NewRequest{Requester: db.NoticeRequester, Target: "bob", Body: "T4 memo added by alice: see the log", Kind: "say", Memo: true})
	r.must(r.SweepDigests(r.clock + 10))
	if len(r.term.typed) != 0 {
		t.Fatalf("a memo's tell skipped the window: %q", r.term.typed)
	}
	r.must(r.SweepDigests(r.clock + 900))
	if !strings.Contains(r.term.last(), "T4 memo added by alice: see the log") {
		t.Fatalf("%q", r.term.last())
	}
}
