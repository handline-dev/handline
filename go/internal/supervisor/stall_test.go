package supervisor

import (
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
)

// stallsTold is how many stall alerts went to who.
func (r *rig) stallsTold(who string) int {
	r.t.Helper()
	var n int
	r.must(r.DB.QueryRow("SELECT count(*) FROM requests WHERE target = ? AND body LIKE '%with no progress for%'", who).Scan(&n))
	return n
}

func TestADoingTaskWithNoProgressFor4hIsToldToItsOwnerOncePerStall(t *testing.T) {
	r := newRig(t)
	base := r.clock
	r.agentUp("bob")
	held := r.newTask("bob", "alice", "the graph")
	_, _, err := r.AdvanceCurrentTask(r.DB, "bob")
	r.must(err)
	// bob holds the graph and does nothing with it; a queued task behind it is not doing and is never told
	r.newTask("bob", "alice", "something else")
	r.must(r.SweepStalls(base + StallS - 60))
	if n := r.stallsTold("alice"); n != 0 {
		t.Fatalf("told before 4h: %d", n)
	}
	r.must(r.SweepStalls(base + StallS + StallCheckEveryS))
	if n := r.stallsTold("alice"); n != 1 {
		t.Fatalf("told %d times at 4h", n)
	}
	var body string
	r.must(r.DB.QueryRow("SELECT body FROM requests WHERE target = 'alice' AND body LIKE '%with no progress for%'").Scan(&body))
	if !strings.Contains(body, "T1 (the graph) is doing with no progress for 4h") || !strings.Contains(body, "bob has taken no turn on it") {
		t.Fatal(body)
	}
	// the next look, the same stall: no second alert
	r.must(r.SweepStalls(base + StallS + 3*StallCheckEveryS))
	if n := r.stallsTold("alice"); n != 1 {
		t.Fatalf("one stall told %d times", n)
	}
	// a memo moves it: no alert until it has stood still for another 4h
	_, err = r.DB.Exec("INSERT INTO task_memos(task_id, author, body, created_at) VALUES(?, 'alice', 'any news?', ?)", held, float64(base+5*3600)+0.5)
	r.must(err)
	r.must(r.SweepStalls(base + 5*3600 + StallS - 60))
	if n := r.stallsTold("alice"); n != 1 {
		t.Fatalf("told again before the memo's 4h: %d", n)
	}
	r.must(r.SweepStalls(base + 5*3600 + StallS + StallCheckEveryS))
	if n := r.stallsTold("alice"); n != 2 {
		t.Fatalf("a second stall told %d times in all", n)
	}
}

func TestTheHoldersTurnsOnItsCurrentTaskAreProgress(t *testing.T) {
	r := newRig(t)
	base := r.clock
	r.agentUp("bob")
	r.newTask("bob", "alice", "the graph")
	_, _, err := r.AdvanceCurrentTask(r.DB, "bob")
	r.must(err)
	_, err = r.DB.Exec("INSERT INTO events(ts, agent, kind, payload) VALUES(?, 'bob', 'Stop', '{}')", base+3*3600)
	r.must(err)
	r.must(r.SweepStalls(base + StallS + StallCheckEveryS))
	if n := r.stallsTold("alice"); n != 0 {
		t.Fatalf("a holder taking turns on it was called stalled: %d", n)
	}
	r.must(r.SweepStalls(base + 3*3600 + StallS + StallCheckEveryS))
	if n := r.stallsTold("alice"); n != 1 {
		t.Fatalf("idle 4h after its last turn: told %d", n)
	}
}

func TestAStalledTasksRowIsClearedOnceItLeavesDoing(t *testing.T) {
	r := newRig(t)
	base := r.clock
	r.agentUp("bob")
	held := r.newTask("bob", "alice", "the graph")
	_, _, err := r.AdvanceCurrentTask(r.DB, "bob")
	r.must(err)
	r.must(r.SweepStalls(base + StallS + StallCheckEveryS))
	if _, found, _ := db.Meta(r.DB, stallKey(held)); !found || r.stallsTold("alice") != 1 {
		t.Fatalf("not alerted and kept: found %v, told %d", found, r.stallsTold("alice"))
	}
	// done, it is no longer doing: the next look drops its row
	_, err = db.MarkTaskDone(r.DB, held, "drawn", "test")
	r.must(err)
	r.must(r.SweepStalls(base + StallS + 2*StallCheckEveryS))
	if _, found, _ := db.Meta(r.DB, stallKey(held)); found {
		t.Fatal("the row of a task no longer doing is kept")
	}
}
