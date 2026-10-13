package supervisor

import (
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
)

func TestTrackBackgroundWorkFollowsTheFooterAndEndsTheSpell(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	row, _ := db.GetAgentState(r.DB, "bob")
	r.term.background = "3 monitors"
	r.must(r.TrackBackgroundWork(r.DB, "bob", row))
	if !r.hasBackgroundWork(r.DB, "bob") {
		t.Fatal("not recorded")
	}
	if n, _ := db.Cursor(r.DB, "bgcount:bob"); n != 1 {
		t.Fatal(n)
	}
	if since, ok := r.bgSince(r.DB, "bob"); !ok || since != r.clock {
		t.Fatal(since, ok)
	}
	r.term.background = ""
	r.must(r.TrackBackgroundWork(r.DB, "bob", row))
	if r.hasBackgroundWork(r.DB, "bob") {
		t.Fatal("still recorded")
	}
	if n, _, _ := db.LastEventID(r.DB, "bob", "background_work_ended"); n == 0 {
		t.Fatal("no end event")
	}
	// a closed session runs nothing
	r.term.background = "1 monitor"
	r.TrackBackgroundWork(r.DB, "bob", row)
	db.SetClosed(r.DB, "bob", "")
	row, _ = db.GetAgentState(r.DB, "bob")
	r.TrackBackgroundWork(r.DB, "bob", row)
	if r.hasBackgroundWork(r.DB, "bob") {
		t.Fatal("a closed session reports work")
	}
}

func TestBackgroundWorkGoesStaleBeforeThePromptCacheLapses(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	row, _ := db.GetAgentState(r.DB, "bob")
	r.term.background = "1 monitor"
	r.TrackBackgroundWork(r.DB, "bob", row)
	start := r.clock
	// The session's own TTL: stale once StaleBgLeadS or less is left, whatever the silence.
	r.sessions.cacheLeft = map[string]int64{"bob": StaleBgLeadS + 1}
	if r.bgStale(r.DB, "bob", start+10) {
		t.Fatal("stale too soon")
	}
	r.sessions.cacheLeft["bob"] = StaleBgLeadS
	if !r.bgStale(r.DB, "bob", start+10) {
		t.Fatal("not stale with the cache about to lapse")
	}
	// A running turn is never forgotten work.
	db.SetAgentState(r.DB, "bob", "RUNNING", 0, "")
	if r.bgStale(r.DB, "bob", start+10) {
		t.Fatal("a running turn is stale")
	}
	db.SetAgentState(r.DB, "bob", "IDLE", 0, "")
	// No TTL known: an hour from what the agent last showed beside the work.
	r.sessions.cacheLeft = nil
	edge := start + states.PromptCacheS - StaleBgLeadS
	if r.bgStale(r.DB, "bob", edge-1) || !r.bgStale(r.DB, "bob", edge) {
		t.Fatal("the fallback clock is not an hour from the silence")
	}
	if silent, ok := r.bgSilentS(r.DB, "bob", start+100); !ok || silent != 100 {
		t.Fatal(silent, ok)
	}
	db.AddEventAt(r.DB, edge-10, "bob", "Stop")
	if r.bgStale(r.DB, "bob", edge) {
		t.Fatal("stale with recent activity")
	}
}

func TestACheckAnswerTurnDoesNotCountAsShownActivity(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	row, _ := db.GetAgentState(r.DB, "bob")
	r.term.background = "1 monitor"
	r.TrackBackgroundWork(r.DB, "bob", row)
	t0 := r.clock
	db.AddEventAt(r.DB, t0+10, "bob", "Stop")
	db.AddEventAt(r.DB, t0+20, "bob", "background_checked")
	db.AddEventAt(r.DB, t0+25, "bob", "UserPromptSubmit")
	db.AddEventAt(r.DB, t0+30, "bob", "Stop") // the check's answer turn ends here
	db.AddEventAt(r.DB, t0+40, "bob", "UserPromptSubmit")
	last, ok := r.bgLastShown(r.DB, "bob")
	if !ok || last != t0+40 {
		t.Fatalf("%d %v", last, ok)
	}
	db.AddEventAt(r.DB, t0+50, "bob", "background_checked")
	db.AddEventAt(r.DB, t0+55, "bob", "UserPromptSubmit")
	if last, _ := r.bgLastShown(r.DB, "bob"); last != t0+40 {
		t.Fatalf("the answer turn counted: %d", last)
	}
}

func TestBgMayStopAnAgentThatOwesNothingAfterItsGraceOrAStaleOneAfterTheVeto(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	row, _ := db.GetAgentState(r.DB, "bob")
	r.term.background = "1 monitor"
	r.TrackBackgroundWork(r.DB, "bob", row)
	now := r.clock + 1000
	if r.bgMayStop(r.DB, "bob", now) {
		t.Fatal("stopped before it was told")
	}
	db.SetCursor(r.DB, "bg_prompt:bob", r.clock)
	if !r.bgMayStop(r.DB, "bob", now) {
		t.Fatal("told and silent past the grace, but not stoppable")
	}
	if r.bgHolds(r.DB, "bob", now) {
		t.Fatal("still holds")
	}
	// something owed: only stale work told to the supervisor and past the veto
	r.ask("alice", "bob", "waiting", "ask")
	if r.bgMayStop(r.DB, "bob", now) {
		t.Fatal("stopped while it owes an answer")
	}
	db.SetCursor(r.DB, "stale_bg_told:bob", now-StaleBgVetoS)
	if r.bgMayStop(r.DB, "bob", now) {
		t.Fatal("not stale yet")
	}
	if !r.bgMayStop(r.DB, "bob", r.clock+states.PromptCacheS-StaleBgLeadS+StaleBgVetoS) {
		t.Fatal("stale and vetoed past its window, but not stoppable")
	}
}

func TestForgottenResolveFindsAToldTaskNobodyAnswered(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	head := r.task("bob", "alice", "the job")
	r.DeliverQueued(r.DB, "bob")
	row, _ := db.GetAgentState(r.DB, "bob")
	if f := r.forgottenResolve(r.DB, "bob", row, r.clock); f == nil || f.Head.ID != head || f.Behind != 0 {
		t.Fatalf("%+v", f)
	}
	if f := r.forgottenResolve(r.DB, "bob", row, r.clock+700); f == nil || f.IdleS != 700 {
		t.Fatalf("%+v", f)
	}
	// it asked something and is waiting on the answer: not forgotten
	r.ask("bob", "alice", "can you check?", "ask")
	if f := r.forgottenResolve(r.DB, "bob", row, r.clock); f != nil {
		t.Fatal("a wait on an ask is not a forgotten resolve")
	}
}

func TestNudgeWalksTheScheduleAndStops(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	head := r.task("bob", "alice", "the job")
	r.DeliverQueued(r.DB, "bob")
	r.term.typed = nil
	row, _ := db.GetAgentState(r.DB, "bob")
	if sent, _ := r.NudgeForgottenResolve(r.DB, "bob", row, r.clock+NudgeAfterS-1); sent {
		t.Fatal("nudged before the first rung")
	}
	r.clock += NudgeAfterS
	sent, err := r.NudgeForgottenResolve(r.DB, "bob", row, r.clock)
	r.must(err)
	if !sent || !strings.HasPrefix(r.term.last(), "T1 (#1) is still yours and you have been idle 10m. Done: hand it on (`hl task handoff T1 ...`) or `hl task done T1`.") {
		t.Fatalf("%v %q", sent, r.term.last())
	}
	if r.nudgeCount(r.DB, "bob", head) != 1 {
		t.Fatal("not counted")
	}
	// not again inside the retry gap, nor before the second rung
	if sent, _ := r.NudgeForgottenResolve(r.DB, "bob", row, r.clock+NudgeRetryS); sent {
		t.Fatal("nudged before the second rung")
	}
	r.clock += ForgottenStuckS
	r.term.typed = nil
	r.DB.Exec("UPDATE events SET ts = ts - 3600 WHERE kind = 'nudged'")
	if sent, _ := r.NudgeForgottenResolve(r.DB, "bob", row, r.clock); !sent {
		t.Fatal("second rung missed")
	}
	r.clock += 2 * 3600
	if sent, _ := r.NudgeForgottenResolve(r.DB, "bob", row, r.clock); !sent {
		t.Fatal("third rung missed")
	}
	if !r.nudgeExhausted(r.DB, "bob", head) {
		t.Fatal("not exhausted")
	}
	r.clock += 10 * 3600
	if sent, _ := r.NudgeForgottenResolve(r.DB, "bob", row, r.clock); sent {
		t.Fatal("typed past the last rung")
	}
}

func TestNudgeForAMessageNamesTheAnswerVerb(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	rid := r.ask("alice", "bob", "can you look?", "ask")
	// an ask is typed at once and stays open until answered; make it the told head
	r.TellNow(r.DB, rid)
	db.SetCursor(r.DB, "notify:bob", rid)
	if f := r.forgottenResolve(r.DB, "bob", mustState(r, "bob"), r.clock+1000); f != nil {
		t.Fatal("only a task head is forgotten")
	}
}

func mustState(r *rig, agent string) *db.AgentState {
	s, err := db.GetAgentState(r.DB, agent)
	r.must(err)
	return s
}
