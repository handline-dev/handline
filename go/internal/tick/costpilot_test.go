package tick

import (
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
)

func ptr[T any](v T) *T { return &v }

// renewing is a world with an idle open agent whose cache is nearly spent and whose switch is on.
func renewing(t *testing.T) (*Tick, *world, *db.AgentState) {
	tk, w := newWorld(t)
	setState(t, tk, "a", states.Idle, 0)
	w.cfg = map[string]any{RenewalKey: true}
	w.noteEnabled, w.askOK = true, true
	w.status = Status{TrustedCtxUsed: ptr(100_000), TranscriptPath: "/t.jsonl"}
	w.cache = &CacheClock{TTL: ptr(int64(3600)), Left: func(int64) *float64 { return ptr(100.0) }}
	row, _ := db.GetAgentState(tk.Q, "a")
	return tk, w, row
}

func TestADueRenewalAsksForTheNoteAndRecordsTheFiguresItWasDecidedOn(t *testing.T) {
	tk, w, row := renewing(t)
	if !tk.Cost.Renew(tk, "a", row, 10_000, w.status) {
		t.Fatalf("not renewed: %v", w.calls)
	}
	if !w.did("ask a cache") || countEvents(t, tk, "a", RenewalKey) != 1 || !w.logged("cache renewal 1 of 2") {
		t.Fatalf("%v %v", w.calls, w.logs)
	}
	// A second in the same stretch is allowed, a third is not.
	if !tk.Cost.Renew(tk, "a", row, 10_100, w.status) {
		t.Fatal("the second renewal of a stretch")
	}
	if tk.Cost.Renew(tk, "a", row, 10_200, w.status) {
		t.Fatal("a third renewal in one idle stretch")
	}
}

func TestARenewalIsNotDueWhenAnyOfItsConditionsFails(t *testing.T) {
	cases := map[string]func(w *world){
		"switch off":           func(w *world) { w.cfg = map[string]any{} },
		"notes off":            func(w *world) { w.noteEnabled = false },
		"context unknown":      func(w *world) { w.status.TrustedCtxUsed = nil },
		"context over the max": func(w *world) { w.status.TrustedCtxUsed = ptr(RenewCtxMax + 1) },
		"no cache clock":       func(w *world) { w.cache = nil },
		"a five minute cache":  func(w *world) { w.cache.TTL = ptr(int64(300)) },
		"too early":            func(w *world) { w.cache.Left = func(int64) *float64 { return ptr(float64(RenewLeadS)) } },
		"too late":             func(w *world) { w.cache.Left = func(int64) *float64 { return ptr(10.0) } },
		"ask not yet taken":    func(w *world) { w.asked, w.askedTurns = true, 0 },
		"background work":      func(w *world) { w.bgWork = true },
		"at the wall":          func(w *world) { w.atLimit = &Limit{Text: "limit"} },
	}
	for name, mutate := range cases {
		tk, w, row := renewing(t)
		mutate(w)
		if tk.Cost.RenewalDue(tk, "a", row, 10_000, w.status) {
			t.Errorf("%s: a renewal was due", name)
		}
	}
	// A running agent is not idle.
	tk, w, _ := renewing(t)
	setState(t, tk, "a", states.Running, 0)
	row, _ := db.GetAgentState(tk.Q, "a")
	if tk.Cost.RenewalDue(tk, "a", row, 10_000, w.status) {
		t.Error("a running agent was renewed")
	}
}

func TestARenewalWhoseAskWasNotSentLeavesNoRecord(t *testing.T) {
	tk, w, row := renewing(t)
	w.askOK = false
	if tk.Cost.Renew(tk, "a", row, 10_000, w.status) || countEvents(t, tk, "a", RenewalKey) != 0 {
		t.Fatal("a renewal that was not asked for was recorded")
	}
}

func TestAPromptAfterARenewalIsItsOwnTurnAndAMessageEndsTheStretch(t *testing.T) {
	tk, w, row := renewing(t)
	for _, kind := range []string{"cache_renewal", "cache_renewal"} {
		if _, err := db.AddEvent(tk.Q, "a", kind); err != nil {
			t.Fatal(err)
		}
	}
	if tk.Cost.RenewalDue(tk, "a", row, 10_000, w.status) {
		t.Fatal("two renewals already made")
	}
	if _, err := db.AddEvent(tk.Q, "a", "UserPromptSubmit"); err != nil {
		t.Fatal(err)
	}
	if tk.Cost.RenewalDue(tk, "a", row, 10_000, w.status) {
		t.Fatal("the prompt straight after a renewal is that renewal's own turn and does not end the stretch")
	}
	db.AddEvent(tk.Q, "a", "SessionStart")
	if !tk.Cost.RenewalDue(tk, "a", row, 10_000, w.status) {
		t.Fatal("a new session starts a new stretch")
	}
}

// folding is a world with an idle open agent over its fold size with nothing to do.
func folding(t *testing.T) (*Tick, *world, *db.AgentState) {
	tk, w := newWorld(t)
	setState(t, tk, "a", states.Idle, 0)
	w.cfg = map[string]any{FoldKey: true}
	w.status = Status{SessionID: "s1", TrustedCtxUsed: ptr(200_000)}
	w.idleSince, w.idleHave, w.closeOK = 10_000-FoldIdleS-1, true, true
	row, _ := db.GetAgentState(tk.Q, "a")
	return tk, w, row
}

func TestAnIdleSessionOverItsFoldSizeIsClosedFreshAndTheFoldIsRecorded(t *testing.T) {
	tk, w, row := folding(t)
	if !tk.Cost.FoldDue(tk, "a", row, 10_000, w.status) {
		t.Fatal("not due")
	}
	w.noteText = "my note"
	ok, why := tk.Cost.Fold(tk, "a", 10_000, w.status)
	if !ok || why != "" || !w.did("closefresh a") || countEvents(t, tk, "a", FoldKey) != 1 || !w.logged("folded - idle 10m at 200,000 tokens, closed fresh") {
		t.Fatalf("%v %q %v %v", ok, why, w.calls, w.logs)
	}
	if w.logged("(no note)") {
		t.Fatalf("the note was written: %v", w.logs)
	}
	// Without a note it says so; a close that failed is not recorded as a fold.
	tk, w, _ = folding(t)
	if _, _ = tk.Cost.Fold(tk, "a", 10_000, w.status); !w.logged("(no note)") {
		t.Fatalf("%v", w.logs)
	}
	tk, w, _ = folding(t)
	w.closeOK, w.closeWhy = false, "mid-turn"
	if ok, why := tk.Cost.Fold(tk, "a", 10_000, w.status); ok || why != "mid-turn" || countEvents(t, tk, "a", FoldKey) != 0 {
		t.Fatalf("%v %q", ok, why)
	}
}

func TestAFoldIsNotDueWhenTheAgentHoldsWorkOrHasNotBeenIdleLongEnough(t *testing.T) {
	cases := map[string]func(tk *Tick, w *world){
		"switch off":         func(tk *Tick, w *world) { w.cfg = map[string]any{} },
		"no session":         func(tk *Tick, w *world) { w.status.SessionID = "" },
		"context unknown":    func(tk *Tick, w *world) { w.status.TrustedCtxUsed = nil },
		"under the size":     func(tk *Tick, w *world) { w.status.TrustedCtxUsed = ptr(FoldCtxMin - 1) },
		"something queued":   func(tk *Tick, w *world) { addRequest(t, tk, "a") },
		"waiting to be told": func(tk *Tick, w *world) { w.waiting = true },
		"not idle long":      func(tk *Tick, w *world) { w.idleSince = 10_000 - 10 },
		"no idle clock":      func(tk *Tick, w *world) { w.idleHave = false },
		"holds a task": func(tk *Tick, w *world) {
			tid, _ := db.NewTask(tk.Q, "x", "a", "boss", 0, db.DefaultPriority, "report", 0, "")
			db.SetCurrentTask(tk.Q, "a", tid, true)
		},
		"a close just failed": func(tk *Tick, w *world) { db.AddEventAt(tk.Q, 10_000-60, "a", "close_failed") },
	}
	for name, mutate := range cases {
		tk, w, row := folding(t)
		mutate(tk, w)
		if tk.Cost.FoldDue(tk, "a", row, 10_000, w.status) {
			t.Errorf("%s: a fold was due", name)
		}
	}
	// The note's own turn restarts the idle clock; it is the answer the fold was held for, so a short idle no longer holds it.
	tk, w, row := folding(t)
	w.idleSince, w.answered = 10_000-5, true
	if !tk.Cost.FoldDue(tk, "a", row, 10_000, w.status) {
		t.Error("the note was answered and the fold still waited")
	}
	// A configured fold size and wait override the defaults.
	tk, w, row = folding(t)
	w.cfg = map[string]any{FoldKey: true, "idle_fold_tokens": 250_000, "idle_fold_after_s": 5}
	if tk.Cost.FoldDue(tk, "a", row, 10_000, w.status) {
		t.Error("the agent's own fold size was ignored")
	}
	w.status.TrustedCtxUsed = ptr(260_000)
	if !tk.Cost.FoldDue(tk, "a", row, 10_000, w.status) {
		t.Error("the agent's own fold size and wait were not used")
	}
}
