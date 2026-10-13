package session

import (
	"fmt"
	"strings"
	"unicode/utf8"

	"github.com/handline/handline/go/internal/term"
	"github.com/handline/handline/go/internal/timefmt"
)

// An agent ocp has work for and cannot give it does not sit silently.
//
// Every path that types at an agent refuses when the screen says a line would not land, and refusing is right. It is also all that
// happened: the agent read as idle, the work stayed queued, and the daemon did not type, tick after tick, while nobody was told.
// This is the part that tells them.
//
// A block is a fact about an agent and its work, not a state of the agent. The daemon keeps trying and carries on the tick the
// obstacle goes, so nothing here is a state a person has to clear (STALLED is that); a delivery_blocks row stands while the work
// waits and the same obstacle holds, is on the founder's list from ShowAfterS, and becomes one request to the founder at
// DecisionAfterS. It is written by the daemon and read by the bridge and `hl ps`, which are other processes, so it lives in the
// database.
//
// ocp names what is in the way, and presses at most one key at a dialog: Esc, and only where DecliningKey admits it: the dialog
// says Esc cancels and offers an option that just declines, or it is the one dialog whose Esc was measured. It never chooses: a
// press lands on the dialog or changes nothing, except where a person has typed into that agent's input box, which Esc can clear.
// What it cleared it reports, and a dialog that keeps coming back is a loop the founder is told about, not one it keeps pressing.
//
// A dialog is also in the way of ocp's own exit and clear, which no waiting outlasts: PastDialog gets past it by the same rules
// before either is typed, and one it cannot get past stops the agent.
const (
	// ShowAfterS: the same obstacle, with work waiting, this long before it is said. A dialog does not clear itself, so the wait
	// is only for the person who is at the terminal and answers within a minute or two; longer and an agent sits on the
	// founder's own answer with the founder not knowing.
	ShowAfterS = 180
	// DecisionAfterS is long enough that someone at the bridge has seen the list and acted, short enough that nobody learns of it
	// from an agent that has been silent for an hour. Later than ShowAfterS: a request stays until someone closes it if the
	// daemon cannot, and the list clears itself.
	DecisionAfterS = 600
	// RecheckS: what each check costs an agent is a screen read and a terminal listing, so a block is re-read this often and no
	// more; a block that is not due is left as it was.
	RecheckS = 10
	// PressAfterS: a person at the terminal reads a dialog for a minute before it is theirs to answer; ocp's own /exit has no
	// person in it and is pressed at once. Shorter than ShowAfterS, so a dialog ocp clears is never on the founder's list at all:
	// it is reported once, as done.
	PressAfterS = 60
	// MaxPresses: a dialog that returns after ocp cleared it is a loop. Idle agents are closed every IDLE_CLOSE_S (10 minutes),
	// so a close that meets background shells raises the same dialog on each pass: one clear is an event, two could be chance,
	// and the third within the window is the pattern. Then ocp stops pressing and the founder is told at once, not at
	// DecisionAfterS.
	MaxPresses   = 3
	PressWindowS = 6 * 3600
	// MaxIgnored: Esc that leaves the dialog standing is not pressed again and again.
	MaxIgnored = 2
	// MaxPressesAgent: the per-dialog count is keyed on the dialog's own title, which is text an agent's screen paints: a counter
	// or a timestamp in it (no malice required) outruns MaxPresses by never repeating. This is the absolute stop that does not
	// depend on the key: however many different titles, ocp does not clear more than this many dialogs on one agent in the same
	// window. Well above MaxPresses, so an agent that genuinely meets a few different, legitimate dialogs is not the case this
	// catches.
	MaxPressesAgent = 10

	// ExitOrigin: `/exit` over background work raises a dialog that waits for a person, and an agent can sit on it for hours with
	// nothing queued, so the sweep looks for that one dialog on every agent, not only where work waits. Its block has this origin;
	// it is read every ProbeS, and pressed like any other after PressAfterS.
	ExitOrigin = "exit"
	ProbeS     = 30
)

// The obstacles that are not a screen's own.
const (
	NoTerminal = "no terminal"
	NoSession  = "no session"
	StalledOb  = "stalled"
	HostDown   = "host down"
	// TableMiss: the process table lists no session for an agent whose terminal draws one. Work is typed at it on the screen's
	// word, so this is not an obstacle; it is a fault in the table that the founder is told about (the bridge, `hl ps`, and a
	// request once it outlasts DecisionAfterS), with how each session-looking process was read, since the table missing a live
	// session is what starved the auditor for hours.
	TableMiss   = "table miss"
	TableOrigin = "table"
)

// Advice is what to do about each, in the words of the bridge's own keys. STALLED's is `w`, not the old `u`/unstall: that key was folded into
// wake, behind the same confirmation.
var Advice = map[string]string{
	term.Modal:        "answer it in its terminal (t on the agents panel)",
	term.Backgrounded: "open its terminal (t on the agents panel) and press Esc to return to the conversation",
	term.Unreadable:   "check its terminal (t on the agents panel); ocp cannot read its screen",
	NoTerminal:        "t on the agents panel starts one",
	NoSession:         "w on the agents panel wakes it",
	StalledOb:         "w on the agents panel clears it",
	HostDown:          "check the terminal host and PATH; ocp cannot reach it at all",
	TableMiss:         "the agent is being given its work on the screen's word; report this, with the pids named, so the process table's miss can be found",
}

// Label is each obstacle in a few words.
var Label = map[string]string{
	term.Modal: "dialog open", term.Backgrounded: "conversation in the background", term.Unreadable: "screen unreadable",
	HostDown: "host unreachable", NoTerminal: "no terminal", NoSession: "no session", StalledOb: "stalled",
	TableMiss: "process table misses a live session",
}

func label(kind string) string {
	if l, ok := Label[kind]; ok {
		return l
	}
	return kind
}

// Reason is what is in the way, as a sentence.
func Reason(kind, detail string) string {
	switch kind {
	case term.Modal, term.Backgrounded, term.Unreadable:
		return term.Blocker{Kind: kind, Detail: detail}.Reason()
	}
	if detail != "" {
		return label(kind) + ": " + detail
	}
	return label(kind)
}

// Reason is the package's Reason, for a port.
func (e *Env) Reason(kind, detail string) string { return Reason(kind, detail) }

// DecisionAfterS is how long a dialog stands before the founder is told.
func (e *Env) DecisionAfterS() int64 { return DecisionAfterS }

// Headline is the short form for a row with little room: the label and, for a dialog, only its title.
func Headline(kind, detail string) string {
	head := detail
	if kind == term.Modal {
		head = strings.SplitN(detail, " - ", 2)[0]
	}
	if head != "" {
		return label(kind) + ": " + head
	}
	return label(kind)
}

// Found is what is in the way: the kind, the detail, and the dialog's own title (empty for every kind but Modal).
type Found struct{ Kind, Detail, Title string }

func clip(s string, n int) string {
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	return string([]rune(s)[:n])
}

func (e *Env) getTerminal(agent string) string {
	row, _ := e.GetAgentState(agent)
	if row == nil {
		return ""
	}
	return row.Terminal
}

// TableMissOf is what is in the way when the process table lists no session for this open agent but its terminal draws one; nil
// otherwise, and also when the table could not be read, which says nothing either way.
func (e *Env) TableMissOf(row *AgentRow) *Found {
	if row.ClosedAt != 0 || row.Terminal == "" || !e.Term.Available() {
		return nil
	}
	if listed := e.Delivery.TableListsSession(row.Agent); listed == nil || *listed {
		return nil
	}
	if !e.Delivery.SessionStillDrawn(row.Terminal, false) {
		return nil
	}
	seen := ""
	if e.Observer != nil {
		seen = clip(strings.Join(e.Observer.Diagnose(), "; "), 300)
	}
	return &Found{TableMiss, "its terminal draws a session the process table does not list - it is given its work " +
		"on the screen's word. How the table read each session-looking process: " + seen, ""}
}

// WorkWaiting is what ocp wants to type at this agent and has not: the head of its queue while it is untold, answers it asked for,
// and the reminder to resolve a request it has sat idle on. Empty when there is nothing.
func (e *Env) WorkWaiting(agent string, row *AgentRow, now int64) (string, error) {
	d := e.Delivery
	var parts []string
	head := d.EffectiveHead(agent)
	notified, err := e.GetCursor(fmt.Sprintf("notify:%s", agent))
	if err != nil {
		return "", err
	}
	if head != nil && (notified != head.ID || d.SessionChanged(agent)) {
		s := fmt.Sprintf("request #%d", head.ID)
		if behind := d.QueueDepth(agent) - 1; behind > 0 {
			s += fmt.Sprintf(" (+%d behind)", behind)
		}
		parts = append(parts, s)
	}
	if answers := d.UnannouncedAnswers(agent); len(answers) > 0 {
		var ids []string
		for _, id := range answers[:min(3, len(answers))] {
			ids = append(ids, fmt.Sprintf("#%d", id))
		}
		s := "answer " + strings.Join(ids, ", ")
		if len(answers) > 3 {
			s += fmt.Sprintf(" (+%d)", len(answers)-3)
		}
		parts = append(parts, s)
	}
	idle := d.ForgottenResolve(agent, row, now)
	nudged, err := e.GetCursor(fmt.Sprintf("nudge:%s", agent))
	if err != nil {
		return "", err
	}
	if idle != nil && idle.IdleS >= d.NudgeAfterS() && nudged != idle.Head.ID && !d.HeadTaskBlocked(agent, idle.Head) {
		parts = append(parts, fmt.Sprintf("reminder to resolve #%d", idle.Head.ID))
	}
	return strings.Join(parts, "; "), nil
}

// Classify is what is in the way of giving this agent its work, nil when nothing is. One screen read of its own at most: WaitOf
// may also read this handle, for a different question, and this one still reads fresh.
//
// Left out on purpose: busy, which is what an agent working is and which STUCK_RUNNING_S already judges; a usage wall, which
// resolves when the window does; a closed session, which the wake path brings back and whose failures end in STALLED; and a
// permission prompt, which the hooks already put on the founder's list.
func (e *Env) Classify(agent string, row *AgentRow) *Found {
	if row == nil || row.ClosedAt != 0 || e.WaitOf(row) != nil {
		return nil
	}
	if row.State == Stalled {
		return &Found{StalledOb, row.Detail, ""}
	}
	handle := row.Terminal
	if handle == "" {
		return &Found{NoTerminal, "", ""}
	}
	if !e.Term.Available() {
		// The sibling of Unreadable below: silently skipping this starves every agent with queued work the same way, with
		// nobody told, for as long as the terminal host stays off PATH.
		return &Found{HostDown, "", ""}
	}
	if !e.Term.Alive(handle) {
		return &Found{NoTerminal, "its terminal is gone - Orca no longer has it", ""}
	}
	if e.Delivery.WallReason(agent, row) != "" {
		return nil
	}
	if row.State != Running && row.State != Handoff && !e.Delivery.SessionPresent(agent) {
		return &Found{NoSession, "", ""}
	}
	seen := e.Term.ScreenBlocker(handle)
	if seen == nil {
		return nil
	}
	return &Found{seen.Kind, seen.Detail, seen.Title}
}

// ExitDialog is what is in the way when this agent's screen shows the exit dialog over background work, else nil. The one dialog
// that is looked for with nothing waiting: it comes from a `/exit` and nothing else, so no working agent is at it and nobody but a
// person at the terminal is.
func (e *Env) ExitDialog(row *AgentRow) *Found {
	if row.ClosedAt != 0 || row.Terminal == "" || !e.Term.Available() {
		return nil
	}
	seen := e.Term.ScreenBlocker(row.Terminal)
	if seen == nil || seen.Kind != term.Modal || strings.ToLower(strings.TrimSpace(seen.Title)) != term.ExitOverWorkTitle {
		return nil
	}
	return &Found{seen.Kind, seen.Detail, seen.Title}
}

// NoteCloseFailure: ocp typed `/exit` and the session is still there. If the screen shows why, it is on the list at once, since
// the dialog is ocp's own doing and nobody has been given the minutes the list otherwise waits for.
func (e *Env) NoteCloseFailure(agent string) error {
	handle := e.getTerminal(agent)
	var seen *term.Blocker
	if handle != "" {
		seen = e.Term.ScreenBlocker(handle)
	}
	if seen == nil || seen.Kind != term.Modal {
		return nil
	}
	now := e.now()
	rec, err := e.BlockGet(agent)
	if err != nil || rec != nil {
		return err
	}
	row, _ := e.GetAgentState(agent)
	work, err := e.WorkWaiting(agent, row, now)
	if err != nil {
		return err
	}
	if err := e.BlockOpen(agent, seen.Kind, seen.Detail, work, now, now, "close", seen.Title); err != nil {
		return err
	}
	e.log("  %s: %s - ocp's own /exit raised it", agent, Reason(seen.Kind, seen.Detail))
	return nil
}

// PastDialog gets past a dialog standing between ocp and an exit or clear it is about to type. why is "" when none is open or the
// one that was is now declined; title is the dialog's.
//
// The key and the rules are decline's: Esc, only where DecliningKey admits it, never for a dialog that has stopped being pressed,
// and the press is recorded in the same history, so a dialog that keeps returning is still the loop StoppedPressing counts. The
// caller holds the agent's terminal; unlike the sweep this does not wait out PressAfterS, since the exit is ocp's own and no person
// is answering a dialog it is about to dismiss.
//
// Esc is what ocp presses on any dialog, including the exit dialog that offers to stop background work: ocp never chooses to stop a
// session's running work. A caller that is only deciding whether to begin passes onlyLimit: then nothing is pressed for a dialog
// that waiting may clear because a person is answering it (a permission prompt on a working agent), and it reads as no obstacle at
// all.
func (e *Env) PastDialog(agent string, onlyLimit bool) (why, title string, err error) {
	handle := e.getTerminal(agent)
	var seen *term.Blocker
	if handle != "" {
		seen = e.Term.ScreenBlocker(handle)
	}
	if seen == nil || seen.Kind != term.Modal {
		return "", "", nil
	}
	if onlyLimit && !e.Term.UsageLimitOpen(handle) {
		return "", "", nil
	}
	now := e.now()
	stopped, err := e.StoppedPressing(agent, &Block{Title: seen.Title}, now)
	if err != nil {
		return "", "", err
	}
	if stopped != "" {
		return Reason(seen.Kind, seen.Detail) + "; " + stopped, seen.Title, nil
	}
	outcome, title, why := e.Term.DeclineDialog(handle)
	if outcome == term.NotPressed {
		return Reason(seen.Kind, seen.Detail) + "; ocp did not press it: " + why, seen.Title, nil
	}
	if err := e.PressAdd(agent, now, title, outcome, why); err != nil {
		return "", "", err
	}
	if outcome == term.StillOpen {
		return Reason(seen.Kind, seen.Detail) + "; Esc did not close it", seen.Title, nil
	}
	_ = e.AddEvent(agent, "dialog_declined", map[string]any{"title": title, "key": "Esc", "work": "exit"})
	e.log("  %s: pressed Esc on \"%s\" to get to its exit", agent, title)
	return "", "", nil
}

// StopWorkAndExit chooses "Exit and stop tasks" on the exit dialog over stale background work, if it is exactly that dialog. True
// when the dialog went; anything else leaves it for PastDialog, which declines it. The caller holds the agent's terminal and has
// established the work is stale.
func (e *Env) StopWorkAndExit(agent string) bool {
	handle := e.getTerminal(agent)
	if handle == "" {
		return false
	}
	outcome, title, _ := e.Term.StopWorkAndExit(handle)
	if outcome == term.NotPressed {
		return false
	}
	_ = e.AddEvent(agent, "stale_background_stopped", map[string]any{"title": title, "outcome": outcome})
	e.log("  %s: pressed Enter on \"%s\" - its background work was stale, so it is stopped", agent, title)
	return outcome == term.Cleared
}

// DialogNote is " - <the dialog on its screen>" when that is what ocp's own close met, else "": what a stall on repeated failed
// handoffs adds so its reason is not only a count.
func (e *Env) DialogNote(agent string) string {
	rec, _ := e.BlockGet(agent)
	if rec != nil && rec.Kind == term.Modal {
		return " - " + Reason(rec.Kind, rec.Detail)
	}
	return ""
}

// StoppedPressing is why ocp will not press this agent's dialog again, or "" when it may. rec is the block it is asked about; the
// history is this one dialog's, by its own raw title (rec.Title, from ParseDialog; never reconstructed from Detail, a rendering
// that a title with a quote in it or over DetailChars long does not round-trip through), not the agent's whole 6-hour past.
func (e *Env) StoppedPressing(agent string, rec *Block, now int64) (string, error) {
	title := rec.Title
	all, err := e.PressesSince(agent, now-PressWindowS)
	if err != nil {
		return "", err
	}
	// The absolute stop, checked first and independent of any title matching: a title an agent varies would otherwise never trip
	// the per-dialog count below, since it never repeats. Checking this first must not hand a single looping dialog the per-agent
	// wording: the count alone does not say whether it was one dialog or several, so the titles actually seen decide what is said.
	allCleared := 0
	titles := map[string]bool{}
	for _, p := range all {
		if p.Outcome == "cleared" {
			allCleared++
			titles[p.Title] = true
		}
	}
	if allCleared >= MaxPressesAgent {
		if len(titles) == 1 {
			return fmt.Sprintf("ocp cleared it %d times in %s and it came back each time, so it has stopped pressing", allCleared, timefmt.Ago(PressWindowS)), nil
		}
		return fmt.Sprintf("ocp cleared %d dialogs on this agent in %s (%d different titles), so it has stopped pressing", allCleared, timefmt.Ago(PressWindowS), len(titles)), nil
	}
	var history, pressed []Press
	cleared := 0
	for _, p := range all {
		if p.Title != title {
			continue
		}
		history = append(history, p)
		if p.Outcome == "cleared" {
			cleared++
		}
		if p.Outcome != "not-pressed" {
			pressed = append(pressed, p)
		}
	}
	if cleared >= MaxPresses {
		return fmt.Sprintf("ocp cleared it %d times in %s and it came back each time, so it has stopped pressing", cleared, timefmt.Ago(PressWindowS)), nil
	}
	if len(pressed) >= MaxIgnored {
		allStill := true
		for _, p := range pressed[len(pressed)-MaxIgnored:] {
			allStill = allStill && p.Outcome == "still-open"
		}
		if allStill {
			return "Esc did not close it, so ocp has stopped pressing", nil
		}
	}
	return "", nil
}

// notPressed is the presses at this block's dialog that were not made, since the block began.
func (e *Env) notPressed(agent string, since int64) ([]Press, error) {
	all, err := e.PressesSince(agent, since)
	if err != nil {
		return nil, err
	}
	var out []Press
	for _, p := range all {
		if p.Outcome == "not-pressed" {
			out = append(out, p)
		}
	}
	return out, nil
}

// PressNote is what ocp says about not having cleared this block's dialog, or "".
func (e *Env) PressNote(agent string, rec *Block, now int64) (string, error) {
	if rec.Kind != term.Modal {
		return "", nil
	}
	stopped, err := e.StoppedPressing(agent, rec, now)
	if err != nil || stopped != "" {
		return stopped, err
	}
	said, err := e.notPressed(agent, rec.Since)
	if err != nil || len(said) == 0 {
		return "", err
	}
	return "ocp did not press it: " + said[len(said)-1].Note, nil
}

// splitLines is Python's str.splitlines.
func splitLines(s string) []string {
	var out []string
	var cur []rune
	rs := []rune(s)
	for i := 0; i < len(rs); i++ {
		switch c := rs[i]; c {
		case '\r':
			if i+1 < len(rs) && rs[i+1] == '\n' {
				i++
			}
			fallthrough
		case '\n', '\v', '\f', 0x1c, 0x1d, 0x1e, 0x85, 0x2028, 0x2029:
			out = append(out, string(cur))
			cur = nil
		default:
			cur = append(cur, c)
		}
	}
	if len(cur) > 0 {
		out = append(out, string(cur))
	}
	return out
}

// tellUnpressed tells the founder at once about a dialog ocp holds without pressing: what it says and how to answer, not only a
// log line. Nothing is said when no dialog is open or the screen was unread.
func (e *Env) tellUnpressed(agent, handle, why string) {
	if why == term.NoDialog || why == term.ScreenUnread {
		return
	}
	var shown string
	if d := e.Term.DialogOnScreen(handle); d != nil {
		var opts []string
		for _, o := range d.Options {
			opts = append(opts, fmt.Sprintf("  %d. %s", o.Number, o.Label))
		}
		shown = fmt.Sprintf("\"%s\"\n%s", d.Title, strings.Join(opts, "\n"))
	} else {
		text, _ := e.Term.ScreenText(handle)
		lines := splitLines(text)
		shown = "its screen:\n" + strings.Join(lines[max(0, len(lines)-15):], "\n")
	}
	e.Asker.Report(agent, fmt.Sprintf("%s is at a dialog ocp did not press: %s.\n%s\nAnswer it from the bridge, or in the agent's terminal "+
		"(`tmux -L ocp attach`, window %s).", agent, why, shown, strings.ReplaceAll(agent, ".", "_")), "dialog not pressed")
}

// decline presses Esc at this block's dialog when the rules allow, and says what came of it. The block, or nil when the dialog is
// gone.
func (e *Env) decline(agent string, rec *Block, now int64) (*Block, error) {
	pressAt := rec.Since + PressAfterS
	if rec.Origin == "close" {
		pressAt = rec.Since
	}
	handle := e.getTerminal(agent)
	if now < pressAt || handle == "" {
		return rec, nil
	}
	if stopped, err := e.StoppedPressing(agent, rec, now); err != nil || stopped != "" {
		return rec, err
	}
	outcome, title, why := e.Term.DeclineDialog(handle)
	if outcome == term.NotPressed {
		last, err := e.notPressed(agent, rec.Since)
		if err != nil {
			return rec, err
		}
		if len(last) == 0 || last[len(last)-1].Note != why || last[len(last)-1].Title != title {
			if err := e.PressAdd(agent, now, title, outcome, why); err != nil {
				return rec, err
			}
			e.log("  %s: ocp did not press its dialog - %s", agent, why)
			e.tellUnpressed(agent, handle, why)
		}
		return rec, nil
	}
	if err := e.PressAdd(agent, now, title, outcome, why); err != nil {
		return rec, err
	}
	if outcome == term.StillOpen {
		e.log("  %s: pressed Esc on \"%s\" and it is still open", agent, title)
		return rec, nil
	}
	how := fmt.Sprintf("cleared by ocp: pressed Esc on \"%s\"", title)
	_ = e.AddEvent(agent, "dialog_declined", map[string]any{"title": title, "key": "Esc", "work": rec.Work})
	openFor := "could not be given its work (" + orNothingYet(rec.Work) + ") while it was open"
	if rec.Origin == ExitOrigin {
		openFor = "sat on it with nobody answering"
	}
	e.Asker.Report(agent, fmt.Sprintf("%s.\n%s %s: %s.\nThe dialog's own footer says Esc cancels it, and Esc is a key that only declines it, so that was the "+
		"only key ocp pressed; nothing was chosen. If it comes back %d times in %s ocp stops pressing and tells you.",
		how, agent, openFor, Reason(rec.Kind, rec.Detail), MaxPresses, timefmt.Ago(PressWindowS)), "dialog declined")
	return nil, e.end(agent, rec, how)
}

func orNothingYet(work string) string {
	if work == "" {
		return "nothing yet"
	}
	return work
}

func (e *Env) lastChecked(agent string) int64 {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.checked[agent]
}

func (e *Env) lastProbed(agent string) int64 {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.probed[agent]
}

func (e *Env) markChecked(agent string, now int64) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.checked == nil {
		e.checked = map[string]int64{}
	}
	e.checked[agent] = now
}

func (e *Env) markProbed(agent string, now int64) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.probed == nil {
		e.probed = map[string]int64{}
	}
	e.probed[agent] = now
}

// Sweep runs once per tick, after the agents: open, keep and end each agent's block, and raise the request for those that have
// outlasted DecisionAfterS: one request for all that come due in the same pass.
func (e *Env) Sweep() error {
	now := e.now()
	rows, err := e.AllAgentStates()
	if err != nil {
		return err
	}
	registered := map[string]bool{}
	for _, a := range e.Agents() {
		registered[a] = true
	}
	var due []*Block
	for _, row := range rows {
		agent := row.Agent
		// The human is never typed at, and a row for an agent that is not on the team has nobody to be given anything.
		if !registered[agent] {
			continue
		}
		rec, err := e.track(agent, row, now)
		if err != nil {
			// One agent's unreadable screen is not the tick's failure.
			e.log("  %s: block check failed: %s", agent, err)
			continue
		}
		if rec == nil || rec.RequestID != 0 {
			continue
		}
		ripe := now-rec.Since >= DecisionAfterS
		if !ripe && rec.Kind == term.Modal {
			stopped, err := e.StoppedPressing(agent, rec, now)
			if err != nil {
				return err
			}
			ripe = stopped != ""
		}
		if ripe {
			// A stall the daemon raised itself (a trust screen) is already in front of the founder; the block stays for the bridge.
			if rec.Kind == StalledOb && e.Asker.OpenDecisionsOf(agent) > 0 {
				continue
			}
			due = append(due, rec)
		}
	}
	if len(due) > 0 {
		return e.raise(due, now)
	}
	return nil
}

func (e *Env) track(agent string, row *AgentRow, now int64) (*Block, error) {
	rec, err := e.BlockGet(agent)
	if err != nil {
		return nil, err
	}
	work, err := e.WorkWaiting(agent, row, now)
	if err != nil {
		return nil, err
	}
	if rec != nil && rec.Origin == TableOrigin {
		// The table's miss stands while it does: re-read, and let a real obstacle to giving the work take the agent's one block.
		if now-e.lastChecked(agent) < RecheckS {
			return rec, nil
		}
		e.markChecked(agent, now)
		if work != "" && e.Classify(agent, row) != nil {
			_ = e.BlockClear(agent)
			rec = nil
		} else if e.TableMissOf(row) == nil {
			return nil, e.end(agent, rec, "the table lists it again, or its screen no longer draws one")
		} else {
			return rec, nil
		}
	}
	own := rec != nil && (rec.Origin == "close" || rec.Origin == ExitOrigin)
	if work == "" && !own {
		if rec != nil {
			if err := e.end(agent, rec, "nothing is waiting for it now"); err != nil {
				return nil, err
			}
		}
		if now-e.lastProbed(agent) < ProbeS {
			return nil, nil
		}
		e.markProbed(agent, now)
		found := e.ExitDialog(row)
		if found == nil {
			found = e.TableMissOf(row)
			if found == nil {
				return nil, nil
			}
			if err := e.BlockOpen(agent, found.Kind, found.Detail, "", now, now+ShowAfterS, TableOrigin, ""); err != nil {
				return nil, err
			}
			e.log("  %s: the process table misses its live session - %s", agent, found.Detail)
			return e.BlockGet(agent)
		}
		if err := e.BlockOpen(agent, found.Kind, found.Detail, "", now, now+ShowAfterS, ExitOrigin, found.Title); err != nil {
			return nil, err
		}
		e.log("  %s: %s - nobody is answering it", agent, Reason(found.Kind, found.Detail))
		return e.BlockGet(agent)
	}
	if rec != nil && now-e.lastChecked(agent) < RecheckS {
		return rec, nil
	}
	e.markChecked(agent, now)
	exitOnly := rec != nil && rec.Origin == ExitOrigin && work == ""
	var found *Found
	if exitOnly {
		found = e.ExitDialog(row)
	} else {
		found = e.Classify(agent, row)
	}
	if found == nil {
		if rec != nil {
			why := "it can be given its work again"
			if exitOnly {
				why = "the exit dialog is gone"
			}
			return nil, e.end(agent, rec, why)
		}
		return nil, nil
	}
	if rec == nil {
		if err := e.BlockOpen(agent, found.Kind, found.Detail, work, now, now+ShowAfterS, "tick", found.Title); err != nil {
			return nil, err
		}
		e.log("  %s: work is waiting and cannot be given - %s", agent, Reason(found.Kind, found.Detail))
	} else {
		if rec.Kind != found.Kind {
			e.log("  %s: still cannot be given its work - %s", agent, Reason(found.Kind, found.Detail))
		}
		w := work
		if w == "" {
			w = rec.Work
		}
		if err := e.BlockUpdate(agent, found.Kind, found.Detail, w, now, found.Title); err != nil {
			return nil, err
		}
	}
	if rec, err = e.BlockGet(agent); err != nil || rec == nil {
		return rec, err
	}
	// An agent that is working may be waiting on a person for that very dialog; only an idle one's is pressed, except the exit
	// dialog, which no working agent is at, and whose agent the state may still read as running.
	if rec.Kind == term.Modal && (row.State != Running && row.State != Handoff ||
		strings.ToLower(strings.TrimSpace(rec.Title)) == term.ExitOverWorkTitle) {
		if e.Lifecycle.AutoTrust(agent) {
			return nil, e.end(agent, rec, "its folder-trust prompt was accepted")
		}
		if e.Lifecycle.AutoBypass(agent) {
			return nil, e.end(agent, rec, "its bypass-permissions warning was accepted")
		}
		return e.decline(agent, rec, now)
	}
	return rec, nil
}

func (e *Env) end(agent string, rec *Block, why string) error {
	if err := e.BlockClear(agent); err != nil {
		return err
	}
	e.log("  %s: no longer blocked (%s) - %s", agent, label(rec.Kind), why)
	rid := rec.RequestID
	if rid == 0 {
		return nil
	}
	others, err := e.Blocks()
	if err != nil {
		return err
	}
	for _, b := range others {
		if b.RequestID == rid {
			return nil
		}
	}
	if e.Asker.RequestState(rid) == "queued" {
		e.Asker.ResolveDone(rid, "cleared - "+why)
	}
	return nil
}

func upperFirst(s string) string {
	r, n := utf8.DecodeRuneInString(s)
	if n == 0 {
		return s
	}
	return strings.ToUpper(string(r)) + s[n:]
}

func (e *Env) raise(recs []*Block, now int64) error {
	var lines []string
	for _, r := range recs {
		held := now - r.Since
		note, err := e.PressNote(r.Agent, r, now)
		if err != nil {
			return err
		}
		lead := fmt.Sprintf("%s cannot be given its work (%s).", r.Agent, timefmt.Ago(held))
		if r.Origin == ExitOrigin && r.Work == "" {
			lead = fmt.Sprintf("%s has sat on a dialog nobody is answering (%s).", r.Agent, timefmt.Ago(held))
		}
		advice, ok := Advice[r.Kind]
		if !ok {
			advice = "see `hl ps`"
		}
		s := fmt.Sprintf("%s\n  In the way: %s\n  Waiting: %s\n", lead, Reason(r.Kind, r.Detail), orNothingYet(r.Work))
		if note != "" {
			s += fmt.Sprintf("  %s.\n", upperFirst(note))
		}
		lines = append(lines, s+fmt.Sprintf("  To clear it: %s.", advice))
	}
	text := "[blocked] " + strings.Join(lines, "\n\n") + "\n\nThis closes itself when it can be given its work."
	rid := e.Asker.RaiseDecision(recs[0].Agent, text, "delivery blocked")
	var agents []string
	for _, r := range recs {
		agents = append(agents, r.Agent)
	}
	if err := e.BlockSetRequest(agents, rid); err != nil {
		return err
	}
	e.log("  blocked: raised #%d for %s", rid, strings.Join(agents, ", "))
	return nil
}

// FounderRow is a block on the founder's list, in founder_waits' shape.
type FounderRow struct {
	Kind, Who string
	Red       bool
	Since     int64
	What      string
}

// FounderRows is the blocks that are on the founder's list.
func (e *Env) FounderRows(now int64) ([]FounderRow, error) {
	blocks, err := e.Blocks()
	if err != nil {
		return nil, err
	}
	var rows []FounderRow
	for _, r := range blocks {
		if now < r.ShowAt {
			continue
		}
		what := fmt.Sprintf("%s · waiting: %s", Headline(r.Kind, r.Detail), orNothingYet(r.Work))
		note, err := e.PressNote(r.Agent, r, now)
		if err != nil {
			return nil, err
		}
		if note != "" {
			what += " · " + note
		}
		rows = append(rows, FounderRow{"blocked", r.Agent, true, r.Since, what})
	}
	return rows, nil
}
