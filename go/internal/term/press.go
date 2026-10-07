package term

import (
	"strconv"
	"strings"
	"time"
)

// What a press did.
const (
	Cleared    = "cleared"
	StillOpen  = "still-open"
	NotPressed = "not-pressed"

	NoDialog     = "no dialog is open"
	ScreenUnread = "its screen could not be read"

	// A dialog's own read starts at the ordinary width and, once a footer is seen at all, doubles up to the max before falling
	// back to TruncatedTitle: a long enough command pushes the panel's top rule and title past a 40-line read.
	dialogReadStart = 40
	dialogReadMax   = 400

	PromptPermission = "permission"
	PromptQuestion   = "question"
	PromptDialog     = "dialog"
	PromptNone       = "none"
	PromptUnreadable = "unreadable"
)

// Term reads and presses one host's terminals. Driver says which vendor's wording a handle's screen is read by: the agent the
// daemon recorded there, else the default vendor's. Settle is how long a press is given between reads (PressSettle by default;
// a test sets it to nothing).
type Term struct {
	Backend Backend
	Driver  func(handle string) Driver
	Settle  time.Duration
	Gap     time.Duration // between the keys of one question answer
}

const (
	// How long a dialog is given to go after Esc, read again each time.
	PressSettle      = 500 * time.Millisecond
	pressSettleReads = 4
	questionKeyGap   = 100 * time.Millisecond
)

// New is a Term on b with the driver lookup drv.
func New(b Backend, drv func(handle string) Driver) *Term {
	return &Term{Backend: b, Driver: drv, Settle: PressSettle, Gap: questionKeyGap}
}

func (t *Term) driver(handle string) Driver {
	if t.Driver != nil {
		if d := t.Driver(handle); d != nil {
			return d
		}
	}
	return DriverFor(DefaultVendor)
}

func (t *Term) sleep(d time.Duration) { time.Sleep(d) }

// screen is the rendered screen of handle, or nil when it cannot be read: a source other than the screen is the accumulated
// stream, whose history is the conversation and not what is drawn now.
func (t *Term) screen(handle string, limit int) *Screen {
	s := t.Backend.ReadScreen(handle, limit)
	if s == nil || s.Source != "screen" || s.Tail == nil {
		return nil
	}
	return s
}

// ScreenText is what this terminal is rendering right now; ok is false when it could not be read, never mistaken for "nothing".
func (t *Term) ScreenText(handle string) (string, bool) {
	s := t.screen(handle, 40)
	if s == nil {
		return "", false
	}
	return strings.Join(s.Tail, "\n"), true
}

// Send types text at handle.
func (t *Term) Send(handle, text string, enter bool) (bool, string) {
	return t.Backend.Send(handle, text, enter)
}

// sendKey sends one named key: Enter is a bare Enter, anything else is typed with no Enter.
func (t *Term) sendKey(handle, key string) (bool, string) {
	if key == Enter {
		return t.Send(handle, "", true)
	}
	return t.Send(handle, key, false)
}

func short(s string, n int) string { return cut(s, n) }

// readDialog is (tail, footer, dialog) for handle's screen now: the one read every caller that looks at or presses a dialog goes
// through, so a long command's dialog (found only by widening) is exactly as pressable as it is visible. tail is nil when the
// screen could not be read; footer is -1 when what was read has no dialog on it; dialog can still be nil with both set.
// allowTruncated false is for the daemon's own autonomous presses: pressing Esc on a dialog it could not read the whole of is a
// choice made on too little to make it safely. first is a read the caller already made at the default width.
func (t *Term) readDialog(handle string, allowTruncated bool, first *Screen) (tail []string, footer int, dialog *Dialog) {
	scr := first
	if scr == nil {
		scr = t.screen(handle, dialogReadStart)
	}
	limit := dialogReadStart
	for {
		if scr == nil {
			return nil, -1, nil
		}
		tail = scr.Tail
		footer = footerRow(tail)
		if footer < 0 {
			return tail, -1, nil
		}
		maxed := limit >= dialogReadMax
		hasRule := false
		for _, x := range tail[:footer] {
			if ruleRE.MatchString(strings.TrimSpace(x)) {
				hasRule = true
				break
			}
		}
		if maxed || hasRule {
			return tail, footer, ParseDialog(tail, footer, maxed && allowTruncated)
		}
		limit = min(limit*2, dialogReadMax)
		scr = t.screen(handle, limit)
	}
}

// DialogOnScreen is the confirmation dialog this terminal shows now, parsed, or nil when there is none or it cannot be read.
func (t *Term) DialogOnScreen(handle string) *Dialog {
	_, _, d := t.readDialog(handle, true, nil)
	return d
}

// pressDialog presses the key pick(dialog) names on the dialog on this terminal's screen, if and only if the screen just read
// says it may be pressed. The screen is read here, immediately before the key, and read again after it: what is pressed is what
// is on screen now. Nothing is pressed on a screen that cannot be read, is not a dialog, or has a title or options that do not
// parse; a key that is sent and does not close the dialog is reported as still open, not repeated.
func (t *Term) pressDialog(handle string, pick func(*Dialog) (string, string), keyName string, allowTruncated bool) (outcome, title, why string) {
	tail, footer, dialog := t.readDialog(handle, allowTruncated, nil)
	if tail == nil {
		return NotPressed, "", ScreenUnread
	}
	if footer < 0 {
		return NotPressed, "", NoDialog
	}
	if dialog == nil {
		return NotPressed, "", "its title or options could not be parsed"
	}
	key, why := pick(dialog)
	if key == "" {
		return NotPressed, dialog.Title, why
	}
	if ok, out := t.sendKey(handle, key); !ok {
		return NotPressed, dialog.Title, "the key could not be sent: " + short(out, 120)
	}
	for i := 0; i < pressSettleReads; i++ {
		t.sleep(t.Settle)
		afterTail, afterFooter, after := t.readDialog(handle, allowTruncated, nil)
		if afterTail == nil {
			continue // not evidence either way
		}
		if afterFooter < 0 {
			return Cleared, dialog.Title, ""
		}
		if after != nil && after.Title != dialog.Title {
			// Not the dialog the key was pressed on: that one is gone, whatever this new one is.
			return Cleared, dialog.Title, ""
		}
	}
	return StillOpen, dialog.Title, "the dialog is still on screen after " + keyName
}

// PressOption presses option number of the dialog on screen now, if it has one, and checks that the dialog went.
func (t *Term) PressOption(handle string, number int) (string, string, string) {
	return t.pressDialog(handle, func(d *Dialog) (string, string) { return digitKey(d, number) }, "option "+strconv.Itoa(number), true)
}

// DeclineDialog presses Esc on the dialog on this terminal's screen, if and only if the screen just read says that declines it.
// Never on a truncated read: this is the daemon choosing on its own.
func (t *Term) DeclineDialog(handle string) (string, string, string) {
	return t.pressDialog(handle, DecliningKey, "Esc", false)
}

// StopWorkAndExit presses Enter on the exit dialog over background work, when StoppingKey allows.
func (t *Term) StopWorkAndExit(handle string) (string, string, string) {
	return t.pressDialog(handle, StoppingKey, "Enter", false)
}

// AcceptsTool is whether a permission prompt for tool on this terminal may be said yes to.
func (t *Term) AcceptsTool(handle, tool string) bool { return t.driver(handle).AcceptsTool(tool) }

// AcceptDialog presses the vendor's one-time yes on its prompt to run tool, if and only if the screen just read is exactly that.
func (t *Term) AcceptDialog(handle, tool string) (string, string, string) {
	d := t.driver(handle)
	return t.pressDialog(handle, func(dlg *Dialog) (string, string) { return d.AcceptingKey(dlg, tool) }, "Enter", true)
}

// AnswerDialog presses the number of the option choose(vendor, dialog) names on the dialog on this terminal's screen. The number
// is read off the screen just read, never remembered. A vendor's folder-trust screen is never answered here: that stays with the
// trust roots. A number over 9 is not one key and is refused (choose returns 0 for no answer).
//
// Whether a digit alone selects is not measured. When the digit leaves the same dialog open with the chosen option now the
// highlighted one and the footer saying Enter confirms, Enter is pressed once, on a fresh read.
func (t *Term) AnswerDialog(handle string, choose func(vendor string, d *Dialog) int) (string, string, string) {
	drv := t.driver(handle)
	var pressed []int
	pick := func(d *Dialog) (string, string) {
		if _, shown := drv.TrustScreen(d.Body); shown {
			return "", "a folder-trust screen is answered by the trust roots only"
		}
		number := choose(drv.Name(), d)
		if number == 0 {
			return "", "no standing answer for this dialog"
		}
		key, why := digitKey(d, number)
		if key != "" {
			pressed = append(pressed, number)
		}
		return key, why
	}
	outcome, title, why := t.pressDialog(handle, pick, "the option's number", true)
	if outcome != StillOpen {
		return outcome, title, why
	}
	replaced := false
	confirm := func(d *Dialog) (string, string) {
		if d.Title != title {
			replaced = true // the ruled dialog is gone, another stands where it was
			return "", "another dialog is on screen now"
		}
		if highlightedNumber(d) != pressed[0] {
			return "", "the chosen option is not the highlighted one"
		}
		if !strings.Contains(d.Footer, "Enter to confirm") {
			return "", "its footer does not say Enter confirms"
		}
		return Enter, ""
	}
	o2, t2, w2 := t.pressDialog(handle, confirm, "Enter", true)
	if replaced {
		return Cleared, title, ""
	}
	if o2 != NotPressed {
		return o2, t2, w2
	}
	// It closed between the two reads, or the screen is not one to press on.
	if w2 == NoDialog {
		return Cleared, title, ""
	}
	return StillOpen, title, why
}

// ConfirmationOnScreen is the vendor's one-time permission prompt this terminal shows now, or nil.
func (t *Term) ConfirmationOnScreen(handle string) *Confirmation {
	d := t.DialogOnScreen(handle)
	if d == nil {
		return nil
	}
	return t.driver(handle).Confirmation(d)
}

// AnswerConfirmation sends a person's Yes or No to the permission prompt on this terminal. Pressed only when the screen just read
// is a recognized prompt whose lines are exactly seen, the lines the person answered, and sameWait(), asked at that same moment,
// says it is still the very prompt they were shown: identical words are not identity. The key is the number of the plain Yes or
// of the No, never "don't ask again".
func (t *Term) AnswerConfirmation(handle string, yes bool, seen []string, sameWait func() bool) (string, string, string) {
	drv := t.driver(handle)
	var refused string
	choose := func(_ string, d *Dialog) int {
		found := drv.Confirmation(d)
		switch {
		case found == nil:
			refused = "the screen is not a permission prompt the daemon recognizes"
		case !equalRows(found.Lines, seen) || (sameWait != nil && !sameWait()):
			refused = "the prompt is not the one that was answered"
		default:
			if yes {
				return found.Yes
			}
			return found.No
		}
		return 0
	}
	outcome, title, why := t.AnswerDialog(handle, choose)
	if refused != "" && outcome == NotPressed {
		why = refused
	}
	return outcome, title, why
}

// PromptOnScreen is the ONE prompt this terminal shows now, read once: a permission prompt (Confirmation), a question
// (Question), a dialog (Dialog), none or unreadable. A permission prompt outranks a question, which outranks a plain dialog, so a
// screen that more than one recognizer accepts is one prompt, not several. The folder-trust screen is not one of them: it has its
// own path, and reads as none. An idle screen costs a single read.
func (t *Term) PromptOnScreen(handle string) (kind string, confirmation *Confirmation, question *Question, dialog *Dialog) {
	scr := t.screen(handle, 40)
	if scr == nil {
		return PromptUnreadable, nil, nil, nil
	}
	drv := t.driver(handle)
	if _, shown := drv.TrustScreen(scr.Tail); shown {
		return PromptNone, nil, nil, nil
	}
	_, _, d := t.readDialog(handle, true, scr)
	if d != nil {
		if c := drv.Confirmation(d); c != nil {
			return PromptPermission, c, nil, nil
		}
	}
	if q := drv.AskQuestion(scr.Tail); q != nil {
		return PromptQuestion, nil, q, nil
	}
	if d != nil {
		return PromptDialog, nil, nil, d
	}
	return PromptNone, nil, nil, nil
}

func (t *Term) sendQuestionKeys(handle string, keys []string) (bool, string) {
	for _, k := range keys {
		if ok, out := t.sendKey(handle, k); !ok {
			return false, "a key could not be sent: " + short(out, 120)
		}
		t.sleep(t.Gap)
	}
	return true, ""
}

func (t *Term) questionCleared(handle string, drv Driver) bool {
	for i := 0; i < pressSettleReads; i++ {
		t.sleep(t.Settle)
		scr := t.screen(handle, 40)
		if scr == nil {
			continue
		}
		if drv.AskQuestion(scr.Tail) == nil {
			return true
		}
	}
	return false
}

func sortedChecked(q *Question) []int {
	out := []int{}
	for i, o := range q.Options {
		if o.Checked {
			out = append(out, i)
		}
	}
	return out
}

func sameSet(a, b []int) bool {
	m := map[int]bool{}
	for _, x := range a {
		m[x] = true
	}
	n := map[int]bool{}
	for _, x := range b {
		n[x] = true
	}
	if len(m) != len(n) {
		return false
	}
	for x := range m {
		if !n[x] {
			return false
		}
	}
	return true
}

// answerToggleAndOther is a multiSelect question's boxes checked and Other typed together: toggling a box moves the cursor too,
// so this sends only the toggles first, re-reads the real screen for where the cursor actually landed, and only then reaches for
// Other from there, never the whole guess in one blind sequence.
func (t *Term) answerToggleAndOther(handle string, drv Driver, found *Question, choices []int, text string) (string, string, string) {
	toggle := drv.QuestionToggleKeys(found, choices)
	if toggle == nil {
		return NotPressed, found.Header, "this answer is not one the daemon sends"
	}
	if ok, why := t.sendQuestionKeys(handle, toggle); !ok {
		return NotPressed, found.Header, why
	}
	t.sleep(t.Settle)
	scr := t.screen(handle, 40)
	if scr == nil {
		return StillOpen, found.Header, "the screen could not be re-read after toggling " + pyList(toggle)
	}
	after := drv.AskQuestion(scr.Tail)
	if after == nil {
		return StillOpen, found.Header, "the question closed on its own after toggling " + pyList(toggle)
	}
	read := sortedChecked(after)
	want := append([]int{}, choices...)
	sortInts(want)
	trail := "toggled " + pyList(toggle) + " -> checked " + pyInts(read) + ", cursor now " + strconv.Itoa(after.Cursor) + " (wanted " + pyInts(want) + ")"
	if !sameSet(read, choices) {
		return StillOpen, found.Header, trail + " - not what was toggled, not sent further"
	}
	other := drv.QuestionOtherAfterToggleKeys(after, text)
	if other == nil {
		return NotPressed, found.Header, trail + " - this answer is not one the daemon sends"
	}
	trail += ", then Other's own keys " + pyList(other)
	if ok, why := t.sendQuestionKeys(handle, other); !ok {
		return NotPressed, found.Header, trail + " - " + why
	}
	if t.questionCleared(handle, drv) {
		return Cleared, found.Header, trail
	}
	return StillOpen, found.Header, trail + " - the question is still on screen after its answer"
}

// AnswerQuestion sends a person's answer to the AskUserQuestion prompt on this terminal. Pressed only when the screen just read
// is a recognized prompt whose lines are exactly seen. Unlike a permission prompt's one key, an answer is several keys in a row,
// so the match is made once here, before the first: a key already sent cannot be un-sent on a later mismatch. Whether it landed
// is read off the question card itself going, never off the "Review your answers" screen a multiSelect answer passes through.
func (t *Term) AnswerQuestion(handle string, seen []string, choices []int, text *string) (string, string, string) {
	drv := t.driver(handle)
	scr := t.screen(handle, 40)
	if scr == nil {
		return NotPressed, "", ScreenUnread
	}
	found := drv.AskQuestion(scr.Tail)
	if found == nil {
		return NotPressed, "", NoDialog
	}
	if !equalRows(found.Lines, seen) {
		return NotPressed, found.Header, "the question is not the one that was answered"
	}
	if len(choices) > 0 && text != nil && found.MultiSelect {
		return t.answerToggleAndOther(handle, drv, found, choices, *text)
	}
	keys := drv.AnswerQuestionKeys(found, choices, text)
	if keys == nil {
		return NotPressed, found.Header, "this answer is not one the daemon sends"
	}
	if ok, why := t.sendQuestionKeys(handle, keys); !ok {
		return NotPressed, found.Header, why
	}
	if t.questionCleared(handle, drv) {
		return Cleared, found.Header, ""
	}
	return StillOpen, found.Header, "the question is still on screen after its answer"
}

// ---- trust and bypass screens

func (t *Term) trustFolder(handle string) (string, bool) {
	text, ok := t.ScreenText(handle)
	if !ok {
		return "", false
	}
	return t.driver(handle).TrustScreen(strings.Split(text, "\n"))
}

// TrustPromptShowing is whether this session sits at its vendor's folder-trust screen; an unreadable screen answers false.
func (t *Term) TrustPromptShowing(handle string) bool {
	_, shown := t.trustFolder(handle)
	return shown
}

// TrustPromptFolder is the folder the trust dialog on this terminal's screen names, or "" when it shows none or the folder could
// not be read: never the path a caller has on record for the session, since accepting it accepts whatever is on screen.
func (t *Term) TrustPromptFolder(handle string) string { f, _ := t.trustFolder(handle); return f }

// TrustPromptGone is true only when the screen was read and it is not a folder-trust screen: the answer for something that lets
// go of a stall, which an unreadable screen must not.
func (t *Term) TrustPromptGone(handle string) bool {
	text, ok := t.ScreenText(handle)
	if !ok {
		return false
	}
	_, shown := t.driver(handle).TrustScreen(strings.Split(text, "\n"))
	return !shown
}

// ConfirmTrust presses the vendor's trust answer, blind to which folder is on screen: callers decide whether to press it.
func (t *Term) ConfirmTrust(handle string) (bool, string) {
	if ok, out := t.Send(handle, t.driver(handle).TrustAnswer(), false); !ok {
		return false, "could not accept the trust dialog: " + short(out, 200)
	}
	ok, out := t.Send(handle, "", true)
	if !ok {
		return false, "could not confirm the trust prompt: " + short(out, 200)
	}
	return true, "trusted"
}

// ---- reading a screen the other ways

// SessionOnScreen is whether this terminal's screen shows an agent's session: the vendor's composer box, a dialog's footer, or a
// turn the vendor's screen says is running. A shell prompt is none of those. known is false when the screen cannot be read.
func (t *Term) SessionOnScreen(handle string) (session, known bool) {
	scr := t.screen(handle, 40)
	if scr == nil {
		return false, false
	}
	tail := scr.Tail
	if ShellPromptIn(tail) {
		return false, true
	}
	if ComposerShape(tail) {
		return true, true
	}
	rows := tail
	if n := modalRows + 3; len(rows) > n {
		rows = rows[len(rows)-n:]
	}
	for _, r := range rows {
		for _, m := range modalMarkers {
			if strings.Contains(r, m) {
				return true, true
			}
		}
	}
	_, known = t.driver(handle).WorkingOnScreen(tail)
	return known, true
}

// ScreenBlocker is what on the screen a typed line would answer or merge into, from one read. Fail-closed: a screen that cannot
// be read blocks, because the cost of a wrong no is an answer given to a question nobody asked. The composer's own content is never
// read.
func (t *Term) ScreenBlocker(handle string) *Blocker {
	scr := t.screen(handle, 40)
	if scr == nil {
		return &Blocker{Kind: Unreadable}
	}
	return t.driver(handle).ScreenBlocker(scr.Tail)
}

// BackgroundWork is what the session itself says is still running behind its prompt ("2 monitors"), "" when it says nothing; ok
// is false when its screen could not be read.
func (t *Term) BackgroundWork(handle string) (string, bool) {
	scr := t.screen(handle, 40)
	if scr == nil {
		return "", false
	}
	return FooterBackground(scr.Tail)
}

// IdleWithBackground is whether the screen shows a session idle at its prompt with work running behind it.
func (t *Term) IdleWithBackground(handle string) bool {
	scr := t.screen(handle, 40)
	return scr != nil && t.driver(handle).IdleWithBackground(scr.Tail)
}

// UsageLimitOpen is whether the dialog on this terminal's screen is the usage-limit one, the only dialog that waiting does not
// clear and a person is not answering.
func (t *Term) UsageLimitOpen(handle string) bool {
	scr := t.screen(handle, 40)
	if scr == nil {
		return false
	}
	footer := footerRow(scr.Tail)
	if footer < 0 {
		return false
	}
	d := ParseDialog(scr.Tail, footer, false)
	return d != nil && usageLimitDialog(d)
}

// ModalOpen is whether a modal is on the agent's screen, where typed text would answer it.
func (t *Term) ModalOpen(handle string) bool { return t.ScreenBlocker(handle) != nil }

// LiveTerminals is every handle the host currently has, or nil when it could not say. nil is not an empty set: reading "I could
// not ask" as "there are none" would tear down a healthy team the first time the host was slow to answer.
func (t *Term) LiveTerminals() map[string]bool { return t.Backend.ListTerminals() }

// Alive is whether the host lists handle.
func (t *Term) Alive(handle string) bool { m := t.LiveTerminals(); return m != nil && m[handle] }

// Wait idle: IdleTimeoutMs and IdleProbeMs are how long the daemon waits on a busy agent.
const (
	IdleTimeoutMs = 120_000
	IdleProbeMs   = 3_000
)

// WaitIdle is whether the agent at handle is idle within timeoutMs.
func (t *Term) WaitIdle(handle string, timeoutMs int) bool {
	return t.Backend.WaitIdle(handle, timeoutMs, t.driver(handle).TurnOnScreen)
}

// IdleNow is whether the agent is idle right now, without blocking the caller.
func (t *Term) IdleNow(handle string) bool { return t.WaitIdle(handle, IdleProbeMs) }

// TypeableNow is idle and nothing on screen that a typed line would answer.
func (t *Term) TypeableNow(handle string) bool {
	return (t.IdleNow(handle) || t.IdleWithBackground(handle)) && t.ScreenBlocker(handle) == nil
}

// KeyBytes is what a person's named key is, as the bytes a terminal sends for it; "" for Enter, which is sent as one.
var KeyBytes = map[string]string{"esc": Escape, "enter": "", "up": "\x1b[A", "down": "\x1b[B", "tab": "\t", "shift-tab": "\x1b[Z", "ctrl-c": "\x03",
	"0": "0", "1": "1", "2": "2", "3": "3", "4": "4", "5": "5", "6": "6", "7": "7", "8": "8", "9": "9"}

// SendKeys presses the named keys in order, stopping at the first the host refuses. Nothing here reads the screen: a person who
// presses a key has looked at it.
func (t *Term) SendKeys(handle string, keys []string) (bool, string) {
	for _, k := range keys {
		b, ok := KeyBytes[k]
		if !ok {
			return false, "not a key: " + pyRepr(k)
		}
		var sent bool
		var out string
		if k == "enter" {
			sent, out = t.Send(handle, "", true)
		} else {
			sent, out = t.Send(handle, b, false)
		}
		if !sent {
			return false, k + ": " + short(out, 200)
		}
	}
	return true, ""
}
