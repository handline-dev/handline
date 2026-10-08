package session

import (
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/term"
)

func (b *blockedFixture) sweep() {
	b.t.Helper()
	if err := b.Sweep(); err != nil {
		b.t.Fatal(err)
	}
}

func (b *blockedFixture) block() *Block {
	b.t.Helper()
	r, err := b.BlockGet("backend")
	if err != nil {
		b.t.Fatal(err)
	}
	return r
}

func (b *blockedFixture) founder(at int64) []FounderRow {
	rows, err := b.FounderRows(at)
	if err != nil {
		b.t.Fatal(err)
	}
	return rows
}

func TestADialogInTheWayOfWorkIsSaidOnceAndClearsItself(t *testing.T) {
	b := newBlockedFixture(t)
	b.term.blockers["h1"] = exitDialogBlocker()
	b.sweep()
	rec := b.block()
	if rec == nil || rec.Kind != term.Modal || !strings.Contains(rec.Work, "request #1") {
		t.Fatalf("work waiting and a dialog open is not recorded: %+v", rec)
	}
	if len(b.logs) != 1 {
		t.Errorf("the first sighting says so in the log once: %q", b.logs)
	}
	if len(b.founder(b.t0)) != 0 {
		t.Error("on the founder's list at once")
	}

	b.at(5)
	b.sweep()
	if b.term.reads != 1 {
		t.Errorf("a check inside the recheck window read the screen again: %d reads", b.term.reads)
	}
	b.at(ShowAfterS - 1)
	if len(b.founder(b.now)) != 0 {
		t.Error("on the list one second short of ShowAfterS")
	}
	b.at(ShowAfterS)
	rows := b.founder(b.now)
	if len(rows) != 1 || !rows[0].Red || rows[0].Who != "backend" || rows[0].Since != b.t0 || !strings.Contains(rows[0].What, "Background work is running") ||
		rows[0].Kind != "blocked" {
		t.Fatalf("rows %+v", rows)
	}
	if len(b.ask.decisions) != 0 {
		t.Fatal("a request before DecisionAfterS")
	}

	b.at(DecisionAfterS - 1)
	b.sweep()
	if len(b.ask.decisions) != 0 {
		t.Fatal("a request one second short of DecisionAfterS")
	}
	b.at(DecisionAfterS)
	b.sweep()
	if len(b.ask.decisions) != 1 {
		t.Fatalf("decisions %v", b.ask.decisions)
	}
	body := b.ask.decisions[0].text
	for _, want := range []string{"[blocked] backend cannot be given its work (10m)", "Background work is running", "request #1", "t on the agents panel", "This closes itself"} {
		if !strings.Contains(body, want) {
			t.Errorf("request lacks %q:\n%s", want, body)
		}
	}
	rid := b.block().RequestID
	if rid != 101 {
		t.Errorf("the record holds request %d", rid)
	}

	for _, extra := range []int64{30, 200, 900} {
		b.at(DecisionAfterS + extra)
		b.sweep()
	}
	b.checked = nil // a restart forgets when it last looked
	b.at(DecisionAfterS + 1000)
	b.sweep()
	if len(b.ask.decisions) != 1 {
		t.Errorf("however long it lasts, still one: %d", len(b.ask.decisions))
	}

	b.term.blockers["h1"] = nil // the founder answered it
	b.checked = nil
	b.at(DecisionAfterS + 1100)
	b.sweep()
	if b.block() != nil {
		t.Fatal("the record stayed after it could be given its work")
	}
	if got := b.ask.resolved[rid]; !strings.HasPrefix(got, "cleared - ") {
		t.Errorf("the request did not close itself: %q", got)
	}

	b.term.blockers["h1"] = &term.Blocker{Kind: term.Modal, Detail: `"Another"`, Title: "Another"}
	b.checked = nil
	b.at(2000)
	b.sweep()
	b.at(2000 + DecisionAfterS)
	b.sweep()
	if len(b.ask.decisions) != 2 {
		t.Errorf("a second episode is a second request: %d", len(b.ask.decisions))
	}
}

func TestWorkWaitingNamesTheRequestTheAnswersAndTheReminder(t *testing.T) {
	b := newBlockedFixture(t)
	b.del.depth["backend"] = 3
	b.del.answers["backend"] = []int64{4, 5, 6, 7, 8}
	b.del.forgotten["backend"] = &Forgotten{IdleS: 700, Head: &Request{ID: 9}}
	row, _ := b.GetAgentState("backend")
	want := "request #1 (+2 behind); answer #4, #5, #6 (+2); reminder to resolve #9"
	if got := b.WorkWaiting("backend", row, b.now); got != want {
		t.Errorf("got %q", got)
	}
	b.SetCursor("notify:backend", 1)
	b.SetCursor("nudge:backend", 9)
	b.del.answers["backend"] = []int64{4}
	if got := b.WorkWaiting("backend", row, b.now); got != "answer #4" {
		t.Errorf("a told head and a nudged reminder: %q", got)
	}
	b.del.changed["backend"] = true
	if got := b.WorkWaiting("backend", row, b.now); got != "request #1 (+2 behind); answer #4" {
		t.Errorf("a new session is told again: %q", got)
	}
	b.del.changed["backend"] = false
	b.SetCursor("nudge:backend", 0)
	b.del.forgotten["backend"].IdleS = 599
	if got := b.WorkWaiting("backend", row, b.now); got != "answer #4" {
		t.Errorf("a reminder before NudgeAfterS: %q", got)
	}
	b.del.forgotten["backend"].IdleS = 600
	b.del.taskBlock = true
	if got := b.WorkWaiting("backend", row, b.now); got != "answer #4" {
		t.Errorf("a reminder for a blocked head task: %q", got)
	}
	b.del.head["backend"] = nil
	b.del.answers["backend"] = nil
	b.del.forgotten["backend"] = nil
	if got := b.WorkWaiting("backend", row, b.now); got != "" {
		t.Errorf("nothing waiting: %q", got)
	}
}

func TestClassifyNamesWhatIsInTheWay(t *testing.T) {
	b := newBlockedFixture(t)
	row := func() *AgentRow { r, _ := b.GetAgentState("backend"); return r }
	cases := []struct {
		name string
		set  func()
		want *Found
	}{
		{"nothing in the way", func() {}, nil},
		{"a dialog", func() { b.term.blockers["h1"] = exitDialogBlocker() }, &Found{term.Modal, exitDialogBlocker().Detail, "Background work is running"}},
		{"an unreadable screen", func() { b.term.blockers["h1"] = &term.Blocker{Kind: term.Unreadable} }, &Found{term.Unreadable, "", ""}},
		{"a terminal Orca lost", func() { b.term.blockers["h1"] = nil; b.term.live["h1"] = false }, &Found{NoTerminal, "its terminal is gone - Orca no longer has it", ""}},
		{"no host", func() { b.term.live["h1"] = true; b.term.down = true }, &Found{HostDown, "", ""}},
		{"a usage wall is no obstacle", func() {
			b.term.down = false
			b.term.blockers["h1"] = exitDialogBlocker()
			b.del.wall["backend"] = "limit"
		}, nil},
		{"no session", func() { b.del.wall["backend"] = ""; b.del.present["backend"] = false }, &Found{NoSession, "", ""}},
		{"a running agent needs no session check", func() { b.exec("UPDATE agent_state SET state = 'RUNNING'") }, &Found{term.Modal, exitDialogBlocker().Detail, "Background work is running"}},
		{"stalled", func() { b.exec("UPDATE agent_state SET state = 'STALLED', detail = 'too many'") }, &Found{StalledOb, "too many", ""}},
		{"closed", func() { b.exec("UPDATE agent_state SET closed_at = 5") }, nil},
	}
	for _, c := range cases {
		c.set()
		got := b.Classify("backend", row())
		if (got == nil) != (c.want == nil) || got != nil && *got != *c.want {
			t.Errorf("%s: got %+v, want %+v", c.name, got, c.want)
		}
	}
	b.exec("UPDATE agent_state SET closed_at = NULL, state = 'IDLE', terminal = NULL")
	if got := b.Classify("backend", row()); got == nil || got.Kind != NoTerminal || got.Detail != "" {
		t.Errorf("no terminal: %+v", got)
	}
	if b.Classify("backend", nil) != nil {
		t.Error("no row")
	}
}

func TestAWaitingPermissionPromptIsNotABlock(t *testing.T) {
	b := newBlockedFixture(t)
	b.exec("UPDATE agent_state SET state = 'RUNNING'")
	b.term.blockers["h1"] = &term.Blocker{Kind: term.Modal, Title: "Bash command"}
	id := b.event("backend", "PermissionRequest")
	b.NoteWait("backend", "Bash", map[string]any{"command": "make"}, &id)
	b.sweep()
	if b.block() != nil {
		t.Fatalf("a prompt the hooks already put on the list is a block: %+v", b.block())
	}
}

func TestADialogIsDeclinedAfterPressAfterSAndTheFounderToldOnce(t *testing.T) {
	b := newBlockedFixture(t)
	b.term.blockers["h1"] = exitDialogBlocker()
	b.term.press["h1"] = [3]string{term.Cleared, "Background work is running", ""}
	b.sweep()
	b.at(PressAfterS - 1)
	b.checked = nil
	b.sweep()
	if len(b.term.pressed) != 0 {
		t.Fatalf("pressed before PressAfterS: %v", b.term.pressed)
	}
	b.at(PressAfterS)
	b.checked = nil
	b.sweep()
	if strings.Join(b.term.pressed, ",") != "decline:h1" {
		t.Fatalf("pressed %v", b.term.pressed)
	}
	if b.block() != nil {
		t.Error("the block stayed after the dialog was cleared")
	}
	if len(b.ask.reports) != 1 || b.ask.reports[0].subject != "dialog declined" ||
		!strings.Contains(b.ask.reports[0].text, `cleared by ocp: pressed Esc on "Background work is running".`) ||
		!strings.Contains(b.ask.reports[0].text, "could not be given its work (request #1) while it was open") {
		t.Errorf("reports %+v", b.ask.reports)
	}
	var n int
	b.DB.QueryRow("SELECT COUNT(*) FROM events WHERE kind = 'dialog_declined'").Scan(&n)
	if n != 1 {
		t.Errorf("%d dialog_declined events", n)
	}
	presses, _ := b.PressesSince("backend", 0)
	if len(presses) != 1 || presses[0].Outcome != term.Cleared {
		t.Errorf("presses %+v", presses)
	}
}

func TestADialogThatComesBackIsAThirdTimeNotPressedAndTheFounderToldAtOnce(t *testing.T) {
	b := newBlockedFixture(t)
	b.term.blockers["h1"] = exitDialogBlocker()
	b.term.press["h1"] = [3]string{term.Cleared, "Background work is running", ""}
	for round := int64(0); round < MaxPresses; round++ {
		b.at(round * 100)
		b.checked = nil
		b.sweep()
		b.at(round*100 + PressAfterS)
		b.sweep()
	}
	if len(b.term.pressed) != MaxPresses {
		t.Fatalf("pressed %d times", len(b.term.pressed))
	}
	b.at(MaxPresses * 100)
	b.checked = nil
	b.sweep()
	b.at(MaxPresses*100 + PressAfterS)
	b.sweep()
	if len(b.term.pressed) != MaxPresses {
		t.Fatalf("pressed again after %d clears: %d", MaxPresses, len(b.term.pressed))
	}
	if len(b.ask.decisions) != 1 || !strings.Contains(b.ask.decisions[0].text, "cleared it 3 times in 6h and it came back each time, so it has stopped pressing") {
		t.Fatalf("decisions %+v", b.ask.decisions)
	}
	rows := b.founder(b.now + ShowAfterS)
	if len(rows) != 1 || !strings.Contains(rows[0].What, "so it has stopped pressing") {
		t.Errorf("rows %+v", rows)
	}
}

func TestADialogOcpCannotPressIsHeldAndTheFounderToldWithTheDialog(t *testing.T) {
	b := newBlockedFixture(t)
	b.term.blockers["h1"] = exitDialogBlocker()
	b.term.press["h1"] = [3]string{term.NotPressed, "Background work is running", "its Esc is not measured"}
	b.term.dialogs["h1"] = &term.Dialog{Title: "Background work is running", Options: []term.Option{{Number: 1, Label: "Exit and stop tasks"}, {Number: 2, Label: "Stay"}}}
	b.sweep()
	b.at(PressAfterS)
	b.sweep()
	b.at(PressAfterS + RecheckS)
	b.sweep() // the same note is not told twice
	if len(b.ask.reports) != 1 {
		t.Fatalf("reports %+v", b.ask.reports)
	}
	r := b.ask.reports[0]
	if r.subject != "dialog not pressed" || !strings.Contains(r.text, "backend is at a dialog ocp did not press: its Esc is not measured.") ||
		!strings.Contains(r.text, "\"Background work is running\"\n  1. Exit and stop tasks\n  2. Stay") || !strings.Contains(r.text, "window backend)") {
		t.Errorf("report %q", r.text)
	}
	rec := b.block()
	if rec == nil {
		t.Fatal("the held block went")
	}
	if got := b.PressNote("backend", rec, b.now); got != "ocp did not press it: its Esc is not measured" {
		t.Errorf("press note %q", got)
	}

	// A screen it could not read, or no dialog at all, is not told.
	b2 := newBlockedFixture(t)
	b2.term.blockers["h1"] = exitDialogBlocker()
	b2.term.press["h1"] = [3]string{term.NotPressed, "", term.ScreenUnread}
	b2.sweep()
	b2.at(PressAfterS)
	b2.sweep()
	if len(b2.ask.reports) != 0 {
		t.Errorf("an unread screen was told: %+v", b2.ask.reports)
	}
}

func TestEscThatLeavesTheDialogStandingIsNotPressedAgainAndAScreenIsShownWithoutADialog(t *testing.T) {
	b := newBlockedFixture(t)
	b.term.blockers["h1"] = exitDialogBlocker()
	b.term.press["h1"] = [3]string{term.StillOpen, "Background work is running", "the dialog is still on screen after Esc"}
	for i := int64(0); i < 4; i++ {
		b.at(i * 100)
		b.checked = nil
		b.sweep()
		b.at(i*100 + PressAfterS)
		b.sweep()
	}
	if len(b.term.pressed) != MaxIgnored {
		t.Errorf("pressed %d times, want %d", len(b.term.pressed), MaxIgnored)
	}
	if got := b.PressNote("backend", b.block(), b.now); got != "Esc did not close it, so ocp has stopped pressing" {
		t.Errorf("note %q", got)
	}

	b2 := newBlockedFixture(t)
	b2.term.blockers["h1"] = exitDialogBlocker()
	b2.term.press["h1"] = [3]string{term.NotPressed, "Weird", "its title could not be parsed"}
	b2.sweep()
	b2.at(PressAfterS)
	b2.sweep()
	if len(b2.ask.reports) != 1 || !strings.Contains(b2.ask.reports[0].text, "its screen:\nline1\nline2") {
		t.Errorf("reports %+v", b2.ask.reports)
	}
}

func TestManyDifferentTitlesStillHitTheAbsoluteStop(t *testing.T) {
	b := newBlockedFixture(t)
	for i := 0; i < MaxPressesAgent; i++ {
		b.PressAdd("backend", b.now-10, string(rune('A'+i)), "cleared", "")
	}
	got := b.StoppedPressing("backend", &Block{Title: "never seen"}, b.now)
	if got != "ocp cleared 10 dialogs on this agent in 6h (10 different titles), so it has stopped pressing" {
		t.Errorf("got %q", got)
	}
	b.exec("DELETE FROM dialog_presses")
	for i := 0; i < MaxPressesAgent; i++ {
		b.PressAdd("backend", b.now-10, "same", "cleared", "")
	}
	if got := b.StoppedPressing("backend", &Block{Title: "same"}, b.now); !strings.HasPrefix(got, "ocp cleared it 10 times in 6h") {
		t.Errorf("one title looping: %q", got)
	}
	// Presses outside the window do not count.
	b.exec("DELETE FROM dialog_presses")
	for i := 0; i < MaxPressesAgent; i++ {
		b.PressAdd("backend", b.now-PressWindowS-1, "same", "cleared", "")
	}
	if got := b.StoppedPressing("backend", &Block{Title: "same"}, b.now); got != "" {
		t.Errorf("old presses counted: %q", got)
	}
}

func TestAnExitDialogNobodyAnswersIsFoundWithNothingWaiting(t *testing.T) {
	b := newBlockedFixture(t)
	b.del.head["backend"] = nil
	b.term.blockers["h1"] = exitDialogBlocker()
	b.sweep()
	rec := b.block()
	if rec == nil || rec.Origin != ExitOrigin || rec.Work != "" || rec.ShowAt != b.t0+ShowAfterS {
		t.Fatalf("block %+v", rec)
	}
	if got := b.logs[len(b.logs)-1]; !strings.HasSuffix(got, "- nobody is answering it") {
		t.Errorf("log %q", got)
	}
	// It is not read again inside ProbeS/RecheckS, and goes when the dialog does.
	b.term.blockers["h1"] = nil
	b.at(RecheckS)
	b.sweep()
	if b.block() != nil {
		t.Error("the exit block stayed after the dialog went")
	}
	// Only the exit dialog is looked for with nothing waiting.
	b.at(100)
	b.term.blockers["h1"] = &term.Blocker{Kind: term.Modal, Title: "Something else"}
	b.sweep()
	if b.block() != nil {
		t.Error("another dialog was probed for with nothing waiting")
	}
	b.at(200)
	b.term.blockers["h1"] = exitDialogBlocker()
	b.sweep()
	rows := b.founder(b.now + ShowAfterS)
	if len(rows) != 1 {
		t.Fatalf("rows %+v", rows)
	}
	// Past DecisionAfterS the request leads with the sat-on wording.
	b.at(200 + DecisionAfterS)
	b.term.press["h1"] = [3]string{term.NotPressed, "", term.NoDialog}
	b.sweep()
	if len(b.ask.decisions) != 1 || !strings.Contains(b.ask.decisions[0].text, "backend has sat on a dialog nobody is answering (10m).") {
		t.Errorf("decisions %+v", b.ask.decisions)
	}
}

func TestTheProcessTablesMissIsABlockOfItsOwn(t *testing.T) {
	b := newBlockedFixture(t)
	b.del.head["backend"] = nil
	no := false
	b.del.listed["backend"] = &no
	b.del.drawn["h1"] = true
	b.Observer = fakeDiag{"pid 5: another user's process", "pid 6: OCP_AGENT=x"}
	b.sweep()
	rec := b.block()
	if rec == nil || rec.Kind != TableMiss || rec.Origin != TableOrigin || !strings.Contains(rec.Detail, "How the table read each session-looking process: pid 5: another user's process; pid 6: OCP_AGENT=x") {
		t.Fatalf("block %+v", rec)
	}
	if rows := b.founder(b.t0 + ShowAfterS); len(rows) != 1 || !strings.HasPrefix(rows[0].What, "process table misses a live session: its terminal draws") {
		t.Errorf("rows %+v", rows)
	}

	// It stands while the miss does, and is not re-read inside RecheckS.
	b.at(20)
	b.sweep()
	b.del.listed["backend"] = nil
	b.at(20 + RecheckS - 1)
	b.sweep()
	if b.block() == nil {
		t.Error("re-read inside RecheckS")
	}
	b.at(20 + RecheckS)
	b.sweep()
	if b.block() != nil {
		t.Error("the miss stood after the table could not be read")
	}

	// A real obstacle to giving work takes the one block.
	yes := false
	b.del.listed["backend"] = &yes
	b.del.head["backend"] = nil
	b.at(100)
	b.sweep()
	if r := b.block(); r == nil || r.Origin != TableOrigin {
		t.Fatalf("block %+v", r)
	}
	b.del.head["backend"] = &Request{ID: 2}
	b.term.blockers["h1"] = exitDialogBlocker()
	b.at(200)
	b.sweep()
	if r := b.block(); r == nil || r.Kind != term.Modal || r.Origin != "tick" {
		t.Errorf("block %+v", r)
	}
}

type fakeDiag []string

func (d fakeDiag) Diagnose() []string { return d }

func TestAStalledAgentWithADecisionOpenIsNotRaisedAgain(t *testing.T) {
	b := newBlockedFixture(t)
	b.exec("UPDATE agent_state SET state = 'STALLED', detail = 'trust screen'")
	b.ask.open["backend"] = 1
	b.sweep()
	b.at(DecisionAfterS)
	b.sweep()
	if len(b.ask.decisions) != 0 {
		t.Fatalf("decisions %+v", b.ask.decisions)
	}
	if rec := b.block(); rec == nil || rec.Kind != StalledOb || rec.Detail != "trust screen" {
		t.Errorf("the block does not stay for the bridge: %+v", rec)
	}
	b.ask.open["backend"] = 0
	b.sweep()
	if len(b.ask.decisions) != 1 || !strings.Contains(b.ask.decisions[0].text, "In the way: stalled: trust screen") ||
		!strings.Contains(b.ask.decisions[0].text, "To clear it: w on the agents panel clears it.") {
		t.Errorf("decisions %+v", b.ask.decisions)
	}
}

func TestSeveralBlocksComeDueInOneRequestAndAUnregisteredAgentIsNeverLookedAt(t *testing.T) {
	b := newBlockedFixture(t)
	b.agent("frontend", Idle, "h2")
	b.agent("ghost", Idle, "h3")
	for _, a := range []string{"frontend", "ghost"} {
		b.del.head[a] = &Request{ID: 7}
		b.del.present[a] = true
	}
	b.term.live["h2"], b.term.live["h3"] = true, true
	for _, h := range []string{"h1", "h2", "h3"} {
		b.term.blockers[h] = &term.Blocker{Kind: term.Unreadable}
	}
	b.sweep()
	b.at(DecisionAfterS)
	b.sweep()
	if len(b.ask.decisions) != 1 {
		t.Fatalf("decisions %+v", b.ask.decisions)
	}
	text := b.ask.decisions[0].text
	if !strings.Contains(text, "backend cannot be given") || !strings.Contains(text, "frontend cannot be given") || strings.Contains(text, "ghost") {
		t.Errorf("request %q", text)
	}
	if !strings.Contains(text, "\n\nfrontend cannot") || !strings.Contains(text, "In the way: its screen could not be read") {
		t.Errorf("request %q", text)
	}
	if g, _ := b.BlockGet("ghost"); g != nil {
		t.Error("an unregistered agent got a block")
	}
	if a, _ := b.BlockGet("frontend"); a == nil || a.RequestID != b.block().RequestID {
		t.Errorf("both blocks hold the one request")
	}
}

func TestAutoTrustAndBypassEndABlockOnAnIdleAgent(t *testing.T) {
	b := newBlockedFixture(t)
	b.term.blockers["h1"] = &term.Blocker{Kind: term.Modal, Title: "Do you trust this folder?", Detail: "trust"}
	b.life.trust["backend"] = true
	b.sweep()
	if b.block() != nil || len(b.term.pressed) != 0 {
		t.Fatalf("block %+v pressed %v", b.block(), b.term.pressed)
	}
	if got := b.logs[len(b.logs)-1]; !strings.Contains(got, "its folder-trust prompt was accepted") {
		t.Errorf("log %q", got)
	}
	b.life.trust["backend"] = false
	b.life.bypass["backend"] = true
	b.at(RecheckS)
	b.sweep()
	if b.block() != nil || !strings.Contains(b.logs[len(b.logs)-1], "its bypass-permissions warning was accepted") {
		t.Errorf("block %+v log %q", b.block(), b.logs[len(b.logs)-1])
	}
	// A working agent's dialog is its person's to answer: nothing is pressed or accepted.
	b.life.bypass["backend"] = false
	b.exec("UPDATE agent_state SET state = 'RUNNING'")
	b.term.press["h1"] = [3]string{term.Cleared, "x", ""}
	b.at(2 * RecheckS)
	b.sweep()
	b.at(2*RecheckS + PressAfterS + 1)
	b.sweep()
	if len(b.term.pressed) != 0 || b.block() == nil {
		t.Errorf("pressed %v block %+v", b.term.pressed, b.block())
	}
}

func TestPastDialogDeclinesOnlyWhatMayBeAndSaysWhy(t *testing.T) {
	b := newBlockedFixture(t)
	if why, title := b.PastDialog("backend", false); why != "" || title != "" {
		t.Fatalf("nothing in the way: %q %q", why, title)
	}
	b.term.blockers["h1"] = exitDialogBlocker()
	b.term.press["h1"] = [3]string{term.Cleared, "Background work is running", ""}
	if why, _ := b.PastDialog("backend", true); why != "" || len(b.term.pressed) != 0 {
		t.Fatalf("onlyLimit pressed a dialog that is not the limit: %q %v", why, b.term.pressed)
	}
	if why, title := b.PastDialog("backend", false); why != "" || title != "" || len(b.term.pressed) != 1 {
		t.Fatalf("declined: %q %q %v", why, title, b.term.pressed)
	}
	b.term.usage["h1"] = true
	if why, _ := b.PastDialog("backend", true); why != "" || len(b.term.pressed) != 2 {
		t.Fatalf("the limit dialog with onlyLimit: %q", why)
	}
	b.term.press["h1"] = [3]string{term.NotPressed, "Background work is running", "its Esc is not measured"}
	why, title := b.PastDialog("backend", false)
	if title != "Background work is running" || !strings.HasSuffix(why, "; ocp did not press it: its Esc is not measured") || !strings.HasPrefix(why, "a dialog is open on its screen") {
		t.Errorf("not pressed: %q %q", why, title)
	}
	b.term.press["h1"] = [3]string{term.StillOpen, "Background work is running", "x"}
	if why, _ := b.PastDialog("backend", false); !strings.HasSuffix(why, "; Esc did not close it") {
		t.Errorf("still open: %q", why)
	}
	b.PastDialog("backend", false)
	if why, _ := b.PastDialog("backend", false); !strings.HasSuffix(why, "Esc did not close it, so ocp has stopped pressing") {
		t.Errorf("stopped: %q", why)
	}
}

func TestStopWorkAndExitRecordsWhatItPressed(t *testing.T) {
	b := newBlockedFixture(t)
	if b.StopWorkAndExit("backend") {
		t.Fatal("pressed with nothing to press")
	}
	b.term.press["h1"] = [3]string{term.Cleared, "Background work is running", ""}
	if !b.StopWorkAndExit("backend") {
		t.Fatal("not cleared")
	}
	var payload string
	b.DB.QueryRow("SELECT payload FROM events WHERE kind = 'stale_background_stopped'").Scan(&payload)
	if !strings.Contains(payload, `"outcome": "cleared"`) || !strings.Contains(payload, `"title": "Background work is running"`) {
		t.Errorf("payload %q", payload)
	}
	b.term.press["h1"] = [3]string{term.StillOpen, "Background work is running", ""}
	if b.StopWorkAndExit("backend") {
		t.Error("a dialog still open is reported as gone")
	}
	b.exec("UPDATE agent_state SET terminal = NULL")
	if b.StopWorkAndExit("backend") {
		t.Error("pressed with no terminal")
	}
}

func TestNoteCloseFailurePutsOcpsOwnDialogOnTheListAtOnce(t *testing.T) {
	b := newBlockedFixture(t)
	if err := b.NoteCloseFailure("backend"); err != nil || b.block() != nil {
		t.Fatalf("a clean screen made a block: %v %+v", err, b.block())
	}
	b.term.blockers["h1"] = exitDialogBlocker()
	if err := b.NoteCloseFailure("backend"); err != nil {
		t.Fatal(err)
	}
	rec := b.block()
	if rec == nil || rec.Origin != "close" || rec.ShowAt != b.now || !strings.Contains(rec.Work, "request #1") {
		t.Fatalf("block %+v", rec)
	}
	if got := b.DialogNote("backend"); !strings.HasPrefix(got, " - a dialog is open on its screen") {
		t.Errorf("note %q", got)
	}
	if rows := b.founder(b.now); len(rows) != 1 {
		t.Errorf("not on the list at once: %+v", rows)
	}
	b.at(0)
	b.sweep() // ocp's own close is pressed at once
	b.term.press["h1"] = [3]string{term.Cleared, "Background work is running", ""}
	b.sweep()
	if len(b.term.pressed) != 1 {
		t.Errorf("a close's dialog waited out PressAfterS: %v", b.term.pressed)
	}
	// A block already standing is left as it was.
	if err := b.NoteCloseFailure("backend"); err != nil {
		t.Fatal(err)
	}
	if b.DialogNote("nobody") != "" {
		t.Error("a note for an agent with no block")
	}
}

func TestHeadlineAndReasonAreTheWordsForARow(t *testing.T) {
	if got := Headline(term.Modal, "Title - body of it"); got != "dialog open: Title" {
		t.Errorf("got %q", got)
	}
	if got := Headline(NoSession, ""); got != "no session" {
		t.Errorf("got %q", got)
	}
	if got := Headline("odd", "x - y"); got != "odd: x - y" {
		t.Errorf("got %q", got)
	}
	if got := Reason(NoTerminal, "its terminal is gone"); got != "no terminal: its terminal is gone" {
		t.Errorf("got %q", got)
	}
	if got := Reason(term.Unreadable, "ignored"); got != "its screen could not be read" {
		t.Errorf("got %q", got)
	}
	if got := Reason(term.Backgrounded, "banner"); got != "its conversation moved to the background: banner" {
		t.Errorf("got %q", got)
	}
	if got := Reason("custom", ""); got != "custom" {
		t.Errorf("got %q", got)
	}
}

func TestSplitLinesIsPythons(t *testing.T) {
	for in, want := range map[string]string{"a\nb": "a|b", "a\r\nb\n": "a|b", "": "", "\n": "", "a\rb\x0bc": "a|b|c", "a\n\nb": "a||b"} {
		if got := strings.Join(splitLines(in), "|"); got != want {
			t.Errorf("splitLines(%q) = %q, want %q", in, got, want)
		}
	}
}
