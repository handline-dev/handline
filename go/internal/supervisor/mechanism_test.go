package supervisor

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
)

// breakDB closes the database under the Env: every statement after it fails, as a busy database does.
func (r *rig) breakDB() { r.DB.Close() }

func TestAFailedStatementComesBackAsAnErrorFromEveryExportedReader(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	row, _ := db.GetAgentState(r.DB, "bob")
	req := r.ask("alice", "bob", "x", "ask")
	task, _ := db.GetTask(r.DB, r.newTask("bob", "alice", "job"))
	head, _ := db.GetRequest(r.DB, req)
	r.breakDB()
	q := db.Q(r.DB)
	calls := map[string]func() error{
		"AgentReason":           func() error { _, _, err := r.AgentReason(q, "bob", 1); return err },
		"BgHolds":               func() error { _, err := r.BgHolds(q, "bob", 1); return err },
		"BgLastShown":           func() error { _, _, err := r.BgLastShown(q, "bob"); return err },
		"BgMayStop":             func() error { _, err := r.BgMayStop(q, "bob", 1); return err },
		"BgOwesNothing":         func() error { _, err := r.BgOwesNothing(q, "bob"); return err },
		"BgPromptedAt":          func() error { _, err := r.BgPromptedAt(q, "bob"); return err },
		"BgSilentS":             func() error { _, _, err := r.BgSilentS(q, "bob", 1); return err },
		"BgSince":               func() error { _, _, err := r.BgSince(q, "bob"); return err },
		"BgStale":               func() error { _, err := r.BgStale(q, "bob", 1); return err },
		"CheckHumanResolve":     func() error { _, err := r.CheckHumanResolve(q, head, []int64{1}, nil, "bob", false); return err },
		"ClearOwed":             func() error { _, err := r.ClearOwed(q, "bob"); return err },
		"EffectiveHead":         func() error { _, err := r.EffectiveHead(q, "bob"); return err },
		"EffectiveHeadOrMarked": func() error { _, err := r.EffectiveHeadOrMarked(q, "bob"); return err },
		"EffectiveTaskHead":     func() error { _, err := r.EffectiveTaskHead(q, "bob"); return err },
		"FoldedNotices":         func() error { _, err := FoldedNotices(q, "bob"); return err },
		"ForgottenResolve":      func() error { _, err := r.ForgottenResolve(q, "bob", row, 1); return err },
		"HasBackgroundWork":     func() error { _, err := r.HasBackgroundWork(q, "bob"); return err },
		"HeadTaskBlocked":       func() error { _, err := r.HeadTaskBlocked(q, "bob", head); return err },
		"IdleSince":             func() error { _, _, err := r.IdleSince(q, "bob"); return err },
		"InReplyTo": func() error {
			c := *head
			c.ParentID.Valid, c.ParentID.Int64 = true, head.ID
			_, err := InReplyTo(q, &c)
			return err
		},
		"LandedAlready":      func() error { _, err := r.LandedAlready(q, "bob", req); return err },
		"LinkDelivery":       func() error { return r.LinkDelivery(q, task.ID, "bob", req) },
		"NudgeCount":         func() error { _, err := r.NudgeCount(q, "bob", 1); return err },
		"NudgeExhausted":     func() error { _, err := r.NudgeExhausted(q, "bob", 1); return err },
		"RaiseDecision":      func() error { _, err := r.RaiseDecision(q, &After{}, "bob", "t", "r", "", "", 0, 0); return err },
		"RecordHumanResolve": func() error { return r.RecordHumanResolve(q, req, []int64{task.ID}, nil, "bob") },
		"Report":             func() error { _, err := r.Report(q, &After{}, "bob", "t", "r"); return err },
		"ScreenDrawsSession": func() error { _, err := r.ScreenDrawsSession("bob"); return err },
		"SessionChanged":     func() error { _, err := r.SessionChanged(q, "bob"); return err },
		"StalledOnDialog":    func() error { _, err := r.StalledOnDialog(q, "bob", "x"); return err },
		"TaskReason":         func() error { _, _, _, err := r.TaskReason(q, task, 1); return err },
		"TypeSay":            func() error { return r.TypeSay(q, "bob", "x", "y") },
		"WallReason":         func() error { _, err := r.WallReason(q, "bob", nil); return err },
		"DeliverQueued":      func() error { _, err := r.DeliverQueued(q, "bob"); return err },
		"DeliverAnswers":     func() error { _, err := r.DeliverAnswers(q, "bob"); return err },
		"TellNow":            func() error { _, err := r.TellNow(q, req); return err },
		"LandingSweep":       func() error { return r.LandingSweep(q, "bob") },
		"AdvanceCurrentTask": func() error { _, _, err := r.AdvanceCurrentTask(q, "bob"); return err },
		"Resolve":            func() error { _, _, err := r.Resolve(q, ResolveIn{RID: req}); return err },
		"SweepHolds":         func() error { return r.SweepHolds(1) },
		"SweepTaskQueue":     func() error { return r.SweepTaskQueue(1) },
		"SweepNotices":       func() error { return r.SweepNotices(1) },
	}
	for name, call := range calls {
		func() {
			defer func() {
				if p := recover(); p != nil {
					t.Errorf("%s raised: %v", name, p)
				}
			}()
			if err := call(); err == nil || !strings.Contains(err.Error(), "database") && !strings.Contains(err.Error(), "closed") {
				t.Errorf("%s: err = %v", name, err)
			}
		}()
	}
}

func TestAFailedStatementAfterTheCommitIsLoggedAndSaidNotRaised(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	rid := r.ask("alice", "bob", "x", "ask")
	r.breakDB()
	for name, said := range map[string]string{
		"Push":           r.Push(r.DB, "bob", rid),
		"PushHere":       r.PushHere(r.DB, "bob", 0),
		"PushAnswer":     r.PushAnswer(r.DB, "bob"),
		"PushAnswerHere": r.PushAnswerHere(r.DB, "bob"),
	} {
		if !strings.Contains(said, "failed") {
			t.Errorf("%s said %q", name, said)
		}
	}
	if !r.logged("telling bob failed") {
		t.Fatalf("%v", r.logs)
	}
}

func TestWaitingPromptsToldStepReturnsItsFailure(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	r.ask("alice", "bob", "wake", "ask")
	_, told, ok, err := r.WaitingPrompt(r.DB, "bob")
	r.must(err)
	if !ok {
		t.Fatal("nothing waits")
	}
	r.breakDB()
	if err := told("s1"); err == nil {
		t.Fatal("a failed record was swallowed")
	}
}

func TestWithoutTheProposalsPortNothingIsAnsweredAndAnUnregisteredAskerIsToldInPythonsWords(t *testing.T) {
	r := newRig(t)
	rid := r.ask("alice", "bob", "q", "ask")
	if ok, _, err := r.Resolve(r.DB, ResolveIn{RID: rid, Verdict: "yes"}); err != nil || !ok {
		t.Fatalf("with the port: %v %v", ok, err)
	}
	r.Ports.Proposals = nil
	rid2 := r.ask("alice", "bob", "q2", "ask")
	if _, ok, _, err := r.ResolveWrite(r.DB, ResolveIn{RID: rid2, Verdict: "yes"}); err == nil || ok || !strings.Contains(err.Error(), "proposals port is not wired") {
		t.Fatalf("%v %v", ok, err)
	}
	if _, _, err := r.Say(r.DB, SayIn{Target: "alice", Body: "a", Requester: "bob", ParentID: rid2}); err == nil {
		t.Fatal("a reply closed a request without the proposals port")
	}
	if row, _ := db.GetRequest(r.DB, rid2); row.State != "queued" {
		t.Fatalf("%+v", row)
	}
	if got := r.PushAnswer(r.DB, "stranger"); got != "  stranger is not registered with ocp - nothing to tell" {
		t.Fatalf("%q", got)
	}
}

// There is no hourly cap on asks: a team.yaml that still names the old ask_rate_per_hour, even 0, loads and the ask goes out.
func TestAnOldAskRateInTeamYamlCapsNothing(t *testing.T) {
	r := newRig(t)
	old := strings.Replace(teamYAML, "  max_chain_depth: 4\n", "  max_chain_depth: 4\n  ask_rate_per_hour: 0\n", 1)
	os.WriteFile(filepath.Join(r.home, "team.yaml"), []byte(old), 0o644)
	out, err := r.Ask(r.DB, AskIn{Target: "bob", Body: "hello", Requester: "alice"})
	r.must(err)
	if !out.OK || strings.Contains(out.Message, "this hour") {
		t.Fatalf("%+v", out)
	}
	if s, _ := db.GetAgentState(r.DB, "alice"); s != nil && s.State == "THROTTLED" {
		t.Fatalf("%+v", s)
	}
}

func TestFinishSayUsesTheCallersTransaction(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	// FinishSay settles on the transaction it is given, so a rollback leaves the say queued
	say := r.ask("alice", "bob", "psst", "say")
	row, _ := db.GetRequest(r.DB, say)
	tx, err := r.DB.Begin()
	r.must(err)
	r.FinishSay(tx, row)
	r.must(tx.Rollback())
	if row, _ := db.GetRequest(r.DB, say); row.State != "queued" {
		t.Fatalf("settled behind the transaction's back: %+v", row)
	}
	r.FinishSay(r.DB, row)
	if row, _ := db.GetRequest(r.DB, say); row.State != "landed" {
		t.Fatalf("%+v", row)
	}
}

func TestNotifyCancelledNamesTheTaskStateAndReportsAFailedSend(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	task := r.task("bob", "alice", "the job")
	r.DeliverQueued(r.DB, "bob")
	row, _ := db.GetRequest(r.DB, task)
	r.term.typed = nil
	typed, why, err := r.NotifyCancelled(r.DB, row, "no longer needed")
	r.must(err)
	if !typed || why != "" || r.term.last() != "#1 from alice was cancelled: no longer needed. Stop work on it; it is closed and needs no resolve, and T1 is doing - `hl task list` shows what you hold. `hl read 1` shows it." {
		t.Fatalf("%v %q %q", typed, why, r.term.last())
	}
	r.Agents.(*sessionAgents).live["bob"] = false
	other := *row // the ledger answers the same operation from its first send
	other.ID = 99
	if typed, why, _ := r.NotifyCancelled(r.DB, &other, "x"); typed || why != "bob has no live session" {
		t.Fatalf("%v %q", typed, why)
	}
}

func TestTheExportedReadersAnswerTheirValueWhenNothingFails(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	rid := r.ask("alice", "bob", "q", "ask")
	head, _ := db.GetRequest(r.DB, rid)
	if yes, err := r.SessionPresent("bob"); err != nil || !yes {
		t.Fatal(yes, err)
	}
	if v, err := AnswerVerb(r.DB, head); err != nil || v != "answer with `hl say alice --parent 1 \"<answer>\"`" {
		t.Fatalf("%q %v", v, err)
	}
	if v, err := TellText(r.DB, head, 1, nil, false, nil); err != nil || !strings.HasPrefix(v, "#1 from alice, waiting for you:\nq\n") {
		t.Fatalf("%q %v", v, err)
	}
	if v, err := r.EffectiveHead(r.DB, "bob"); err != nil || v == nil || v.ID != rid {
		t.Fatal(v, err)
	}
	if why, able, err := r.AgentReason(r.DB, "bob", r.clock); err != nil || why != "" || !able {
		t.Fatal(why, able, err)
	}
}
