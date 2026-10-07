package supervisor

import (
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
)

func TestSendJoinsTheMiddleLinesPastThree(t *testing.T) {
	r := newRig(t)
	ok, _ := r.send("%1", "a\nb\nc\nd\ne")
	if !ok || r.term.last() != "a\nb c d\ne" {
		t.Fatalf("%q", r.term.last())
	}
	r.send("%1", "one\ntwo\nthree")
	if r.term.last() != "one\ntwo\nthree" {
		t.Fatalf("%q", r.term.last())
	}
}

func TestTypeGateHoldsForSettleIdleAndOurOwnUnsentText(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	if why := r.typeGate(r.DB, "bob", "%bob", true, true); why != "" {
		t.Fatal(why)
	}
	db.SetCursor(r.DB, "typed_at:bob", r.clock-5)
	if why := r.typeGate(r.DB, "bob", "%bob", true, true); why != "bob was typed at moments ago; letting the screen catch up" {
		t.Fatal(why)
	}
	if why := r.typeGate(r.DB, "bob", "%bob", true, false); why != "" {
		t.Fatalf("an ordinary notify is not held to the settle window: %q", why)
	}
	r.term.typeable = false
	if why := r.typeGate(r.DB, "bob", "%bob", true, false); why != "bob is mid-turn or has a dialog open" {
		t.Fatal(why)
	}
	if why := r.typeGate(r.DB, "bob", "%bob", false, false); why != "" {
		t.Fatal(why)
	}
	r.term.typeable = true
	db.SetMeta(r.DB, "unsent:bob", `{"text": "hello", "at": 1, "enters": 0, "retyped": false, "blocked": false}`)
	r.term.box = "hello"
	if why := r.typeGate(r.DB, "bob", "%bob", true, false); why != "bob has text we typed still unsent in its input box" {
		t.Fatal(why)
	}
}

func TestMarkTypedKeepsWhatWasTypedUntilThePromptIsSeenWithoutIt(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	r.send("%bob", "do the thing")
	r.MarkTyped(r.DB, "bob", nil)
	raw, _, _ := db.Meta(r.DB, "unsent:bob")
	if raw != `{"text": "do the thing", "at": 100000, "enters": 0, "retyped": false, "blocked": false}` {
		t.Fatalf("%s", raw)
	}
	if n, _ := db.Cursor(r.DB, "typed_at:bob"); n != r.clock {
		t.Fatal(n)
	}
	// the box shows our text: the first Enter comes after the grace window
	r.term.box = "do the thing"
	held, err := r.RecoverUnsent(r.DB, "bob")
	r.must(err)
	if !held || len(r.term.typed) != 1 {
		t.Fatalf("pressed Enter inside the grace window: %v %v", held, r.term.typed)
	}
	r.clock += UnsentGraceS
	r.term.typed = nil
	held, _ = r.RecoverUnsent(r.DB, "bob")
	if !held || len(r.term.typed) != 1 || r.term.typed[0] != "" {
		t.Fatalf("no Enter: %v %q", held, r.term.typed)
	}
	// Enter did not take within the wait: clear the box and type again
	r.clock += UnsentEnterWaitS
	r.term.typed = nil
	r.term.box = "do the thing"
	cleared := false
	r.Terminal = &clearingTerm{fakeTerm: r.term, clear: func() { r.term.box = ""; cleared = true }}
	held, _ = r.RecoverUnsent(r.DB, "bob")
	if !held || !cleared || r.term.last() != "do the thing" {
		t.Fatalf("%v %v %v", held, cleared, r.term.typed)
	}
	if !r.logged("Enter did not send our text; cleared the box and typed it again") {
		t.Fatal(r.logs)
	}
	// the prompt is seen without our text: the record is dropped
	r.term.box = ""
	held, _ = r.RecoverUnsent(r.DB, "bob")
	if held {
		t.Fatal("still holding")
	}
	if _, found, _ := db.Meta(r.DB, "unsent:bob"); found {
		t.Fatal("record kept")
	}
}

type clearingTerm struct {
	*fakeTerm
	clear func()
}

func (c *clearingTerm) SendCtrlC(h string) { c.fakeTerm.SendCtrlC(h); c.clear() }

func TestAnUnclearableBoxGivesUpAndIsReported(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	db.SetMeta(r.DB, "unsent:bob", `{"text": "stuck", "at": 1, "enters": 1, "retyped": false, "blocked": false}`)
	r.term.box = "stuck"
	r.clock = 1000
	held, _ := r.RecoverUnsent(r.DB, "bob")
	if !held || !r.logged("the box would not clear; giving up") {
		t.Fatalf("%v %v", held, r.logs)
	}
	if n, _, _ := db.LastEventID(r.DB, "bob", "unsent_input_blocked"); n == 0 {
		t.Fatal("no event")
	}
	held, _ = r.RecoverUnsent(r.DB, "bob")
	why, _, ok := r.HeldReason("bob", r.clock)
	if !held || !ok || why != "text we typed is still unsent in its input box and Enter does not send it" {
		t.Fatalf("%v %q", held, why)
	}
}

func TestTypeNoticeOnlyTypesPastEveryGate(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	ok, err := r.TypeNotice(r.DB, "bob", "a line")
	r.must(err)
	if !ok || r.term.last() != "a line" {
		t.Fatal(ok, r.term.typed)
	}
	r.term.typeable = false
	if ok, _ := r.TypeNotice(r.DB, "bob", "again"); ok {
		t.Fatal("typed at a working agent")
	}
	r.term.typeable = true
	r.Waits = fakeWaits{limit: map[string]bool{"bob": true}}
	if ok, _ := r.TypeNotice(r.DB, "bob", "at the wall"); ok {
		t.Fatal("typed at the wall")
	}
}

func TestWallReasonCoversALimitAndAMovableParkOnly(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	if w := r.WallReason(r.DB, "bob", nil); w != "" {
		t.Fatal(w)
	}
	db.SetAgentState(r.DB, "bob", "PARKED", r.clock+100, "usage")
	r.DB.Exec("INSERT INTO handoff(id, agent, trigger_kind, step, state, updated) VALUES('h', 'bob', 'usage', 'done', 'done', 5)")
	if w := r.WallReason(r.DB, "bob", nil); w != "bob is parked until its usage window rolls over" {
		t.Fatal(w)
	}
	r.DB.Exec("UPDATE handoff SET trigger_kind = 'by hand'")
	if w := r.WallReason(r.DB, "bob", nil); w != "" {
		t.Fatalf("a hand park is not the wall: %q", w)
	}
	r.Waits = fakeWaits{limit: map[string]bool{"bob": true}}
	if w := r.WallReason(r.DB, "bob", nil); w != "bob is at its usage limit" {
		t.Fatal(w)
	}
}

func TestAClearBackgroundWorkHoldsOffIsToldOnceThenTypedWhenItsTimeIsUp(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	r.term.background = "2 monitors"
	ok, why := r.ReclaimContext(r.DB, "bob")
	if ok || why != "background work is running (2 monitors); /clear is owed" {
		t.Fatalf("%v %q", ok, why)
	}
	if !strings.Contains(r.term.last(), "your context is full (5,000 of 200,000), so this session will be cleared in 5 minutes.") {
		t.Fatalf("%q", r.term.last())
	}
	n := len(r.term.typed)
	if r.ClearHold(r.DB, "bob") != "bob is about to be cleared; nothing is typed at it until then" {
		t.Fatal("no hold while the clear is owed")
	}
	if ok, _ := r.ReclaimContext(r.DB, "bob"); ok || len(r.term.typed) != n {
		t.Fatal("told twice")
	}
	r.clock += ClearWaitS
	ok, why = r.ReclaimContext(r.DB, "bob")
	if !ok || why != "cleared" {
		t.Fatalf("%v %q", ok, why)
	}
	if rec, _ := db.GetHandoff(r.DB, "bob"); !strings.Contains(rec, "## Cut off by the clear") {
		t.Fatalf("%q", rec)
	}
	if r.ClearOwed(r.DB, "bob") != 0 {
		t.Fatal("clear still owed")
	}
}

func TestAClearGoesAtOnceWhenTheCacheHasLapsedOrNothingRuns(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	if ok, _ := r.ReclaimContext(r.DB, "bob"); !ok {
		t.Fatal("nothing runs behind the prompt")
	}
	r.term.background = "1 monitor"
	db.AddEventAt(r.DB, r.clock-3700, "bob", "Stop")
	if ok, _ := r.ReclaimContext(r.DB, "bob"); !ok || !r.logged("its cache has lapsed - clearing") {
		t.Fatal(r.logs)
	}
	r.Observer = fakeObserver{map[string]bool{}}
	r.Terminal = noSession{r.term}
	if ok, why := r.ReclaimContext(r.DB, "bob"); ok || why != "no session process; not typing /clear at a shell prompt" {
		t.Fatalf("%v %q", ok, why)
	}
}

func TestADialogStandingLongEnoughStallsTheAgent(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	if r.StalledOnDialog(r.DB, "bob", "a trust dialog") {
		t.Fatal("stalled with no dialog standing")
	}
	db.SetCursor(r.DB, "dialog_since:bob", r.clock-100)
	if r.StalledOnDialog(r.DB, "bob", "a trust dialog") {
		t.Fatal("stalled too soon")
	}
	r.clock += 600
	if !r.StalledOnDialog(r.DB, "bob", "a trust dialog") {
		t.Fatal("not stalled")
	}
	if s, _ := db.GetAgentState(r.DB, "bob"); s.State != "STALLED" || s.Detail.String != "cannot get past a dialog: a trust dialog" {
		t.Fatalf("%+v", s)
	}
}

func TestPastDialogKeepsWhenThisDialogFirstProvedUnpassable(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	r.Dialogs = stubDialogs{why: "a dialog", title: "Trust?"}
	if why := r.pastDialog(r.DB, "bob", false); why != "a dialog" {
		t.Fatal(why)
	}
	first, _ := db.Cursor(r.DB, "dialog_since:bob")
	r.clock += 30
	r.pastDialog(r.DB, "bob", false)
	if again, _ := db.Cursor(r.DB, "dialog_since:bob"); again != first {
		t.Fatal("the same dialog restarted its clock")
	}
	r.Dialogs = stubDialogs{why: "a dialog", title: "Another?"}
	r.pastDialog(r.DB, "bob", false)
	if again, _ := db.Cursor(r.DB, "dialog_since:bob"); again == first {
		t.Fatal("a different dialog kept the old clock")
	}
	r.Dialogs = stubDialogs{}
	if why := r.pastDialog(r.DB, "bob", false); why != "" {
		t.Fatal(why)
	}
	if n, _ := db.Cursor(r.DB, "dialog_since:bob"); n != 0 {
		t.Fatal("clock kept after the dialog went")
	}
}

type stubDialogs struct{ why, title string }

func (s stubDialogs) PastDialog(db.Q, string, bool) (string, string) { return s.why, s.title }
func (stubDialogs) Reason(k, d string) string                        { return k + ": " + d }
func (stubDialogs) DecisionAfterS() int64                            { return 600 }

func TestSessionPresenceNeedsAWitnessAndWaitsAreBounded(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	if !r.SessionPresent("bob") {
		t.Fatal("the table lists it")
	}
	r.Observer = fakeObserver{map[string]bool{}}
	r.Terminal = noSession{r.term}
	if r.SessionPresent("bob") {
		t.Fatal("no witness, still present")
	}
	if r.WaitSessionGone("bob") != true {
		t.Fatal("a session out of the table is gone")
	}
	r.Observer = fakeObserver{map[string]bool{"bob": true}}
	if r.WaitSessionGone("bob") {
		t.Fatal("a session still in the table is gone")
	}
	if r.WaitForSession("bob", 1, "") != true {
		t.Fatal("not waited for")
	}
	if r.WaitForSession("alice", 1, "") {
		t.Fatal("alice has no session")
	}
	// NoteSeen: a fresh sweep reading wins over a new table read
	r.Observer = fakeObserver{map[string]bool{"bob": false}}
	r.NoteSeen(map[string]Seen{"bob": {SessionSeen: true, HasPID: true, SessionPID: 7}})
	if yes, known := r.TableListsSession("bob"); !yes || !known {
		t.Fatal("ignored the sweep's reading")
	}
	r.NoteSeen(map[string]Seen{"bob": {SessionSeen: false}})
	if _, known := r.TableListsSession("bob"); known {
		t.Fatal("an unreadable table is known")
	}
	r.clock += SeenFreshS + 1
	if yes, known := r.TableListsSession("bob"); yes || !known {
		t.Fatal("a stale sweep reading was kept")
	}
}
