package term

import (
	"regexp"
	"strconv"
	"strings"

	"github.com/handline/handline/go/internal/textsafe"
)

// Confirmation is a permission prompt read off a vendor's screen: what the tool call is (Title, Lines verbatim from the panel)
// and the option numbers that say a plain one-time yes and no.
type Confirmation struct {
	Title   string
	Lines   []string
	Yes, No int
}

// QuestionOption is one choice a Question offers, in the vendor's own words. Checked is only ever true on a multiSelect question.
type QuestionOption struct {
	Label       string
	Description string
	Checked     bool
}

// Question is one AskUserQuestion question read off a vendor's screen. Options are the choices Claude itself proposed, never
// "Type something" or "Chat about this". OtherNumber is the "Type something" control's number; OtherText the words typed into it
// ("" and HasOtherText false while it still reads "Type something"). Lines is the panel's own text, quoted the way Dialog.Body
// is. Cursor is the row the "❯" marker is on now.
type Question struct {
	Header       string
	Question     string
	Options      []QuestionOption
	MultiSelect  bool
	OtherNumber  int
	OtherText    string
	HasOtherText bool
	Lines        []string
	Cursor       int
}

// What on the screen a typed line would answer or merge into, named.
const (
	Modal        = "modal"
	Backgrounded = "backgrounded"
	Unreadable   = "unreadable"
)

// Blocker is why a line typed now would not land. Detail is quoted from the screen, never classified; Title is the dialog's own
// title, raw from ParseDialog, for a Modal it could parse.
type Blocker struct {
	Kind   string
	Detail string
	Title  string
}

// Reason is the blocker in words.
func (b Blocker) Reason() string {
	var base string
	switch b.Kind {
	case Modal:
		base = "a dialog is open on its screen"
	case Backgrounded:
		base = "its conversation moved to the background"
	default:
		return "its screen could not be read"
	}
	if b.Detail != "" {
		return base + ": " + b.Detail
	}
	return base
}

// Driver is what a vendor's own screen wording gives the readers (the screen half of handline/vendors).
type Driver interface {
	Name() string
	TurnOnScreen(tail []string) bool
	WorkingOnScreen(tail []string) (working, known bool)
	IdleWithBackground(tail []string) bool
	ScreenBlocker(tail []string) *Blocker
	// TrustScreen is the folder the vendor's folder-trust dialog names when tail shows it; shown is false when it is not
	// showing, and folder "" when it is showing but the folder could not be parsed out.
	TrustScreen(tail []string) (folder string, shown bool)
	BypassScreen(tail []string) (option string, shown bool)
	// TrustAnswer is the keys sent, then Enter, to accept the folder TrustScreen named; BypassAccept the label of the option that
	// accepts the bypass warning and BypassNextKey the key that moves its cursor down one option.
	TrustAnswer() string
	BypassAccept() string
	BypassNextKey() string
	Confirmation(d *Dialog) *Confirmation
	AskQuestion(tail []string) *Question
	// AnswerQuestionKeys is the keys that answer q with choices (0-based) or text, or nil when this is not sent this way.
	AnswerQuestionKeys(q *Question, choices []int, text *string) []string
	QuestionToggleKeys(q *Question, choices []int) []string
	QuestionOtherAfterToggleKeys(q *Question, text string) []string
	AcceptsTool(tool string) bool
	AcceptingKey(d *Dialog, tool string) (string, string)
}

// DefaultVendor is the driver of an agent the daemon did not launch.
const DefaultVendor = "claude"

// DriverFor is the driver for a vendor name; nil for one with none.
func DriverFor(vendor string) Driver {
	switch vendor {
	case "claude":
		return claude{}
	case "opencode":
		return opencode{}
	}
	return nil
}

// ---- claude

type claude struct{}

func (claude) Name() string { return "claude" }

// Text a working claude draws in its status row and an idle one does not.
const workingRows = 12

func (claude) TurnOnScreen(tail []string) bool {
	rows := tail
	if len(rows) > workingRows {
		rows = rows[len(rows)-workingRows:]
	}
	for _, r := range rows {
		if strings.Contains(strings.ToLower(r), "esc to interrupt") {
			return true
		}
	}
	return false
}

// Orca reads claude's working state; the screen carries no turn marker the daemon reads, so it is never the witness.
func (claude) WorkingOnScreen([]string) (bool, bool) { return false, false }

var spinnerRowRE = regexp.MustCompile(`^\S\s+\S+…(\s|$)`)

const spinnerRows = 6

// IdleWithBackground is Claude Code's layout: the hint row counts background work, and neither the spinner nor "esc to
// interrupt" is drawn.
func (claude) IdleWithBackground(tail []string) bool {
	top, bottom, ok := ComposerBox(tail)
	if !ok {
		return false
	}
	if bg, _ := FooterBackground(tail); bg == "" {
		return false
	}
	for _, r := range tail[bottom+1:] {
		if strings.Contains(r, "esc to interrupt") {
			return false
		}
	}
	for _, r := range tail[max(0, top-spinnerRows):top] {
		if spinnerRowRE.MatchString(strings.TrimSpace(r)) {
			return false
		}
	}
	return true
}

// Claude Code's folder-trust screen ("Quick safety check: Is this a project you created or one you trust?"), and the variant for
// a folder whose settings pre-approve tools (Claude Code 2.1.291): the same workspace line and options under a different question.
var trustMarkers = []string{"Is this a project you created or one you trust", "This folder pre-approves"}

// The line the prompt names its folder under. Wrapped at terminal width with no separator, so its continuation lines are joined bare.
const workspaceMarker = "Accessing workspace:"

func (claude) TrustScreen(tail []string) (string, bool) {
	text := strings.Join(tail, "\n")
	shown := false
	for _, m := range trustMarkers {
		shown = shown || strings.Contains(text, m)
	}
	if !shown {
		return "", false
	}
	lines := strings.Split(text, "\n")
	start := -1
	for i, ln := range lines {
		if strings.Contains(ln, workspaceMarker) {
			start = i + 1
			break
		}
	}
	if start < 0 {
		return "", true
	}
	var parts []string
loop:
	for _, ln := range lines[start:] {
		s := strings.TrimSpace(ln)
		if s == "" {
			break
		}
		for _, m := range trustMarkers {
			if strings.Contains(s, m) {
				break loop
			}
		}
		parts = append(parts, s)
	}
	return strings.Join(parts, ""), true
}

// claude's warning when it starts with --dangerously-skip-permissions on a config directory that has not accepted it yet: two
// unnumbered options, the cursor on the one that exits.
const (
	bypassMarker  = "WARNING: Claude Code running in Bypass Permissions mode"
	bypassAccept  = "Yes, I accept"
	bypassDecline = "No, exit"
	bypassRows    = 30
)

// BypassScreen: both options and the confirm hint must be on the last rows, so a transcript that quotes the warning is not it.
func (claude) BypassScreen(tail []string) (string, bool) {
	rows := tail
	if len(rows) > bypassRows {
		rows = rows[len(rows)-bypassRows:]
	}
	var trimmed, labels []string
	hint, marker := false, false
	for _, r := range rows {
		t := strings.TrimSpace(r)
		trimmed = append(trimmed, t)
		labels = append(labels, strings.TrimSpace(strings.TrimLeft(t, "❯")))
		marker = marker || t == bypassMarker
		hint = hint || strings.HasPrefix(t, "Enter to confirm")
	}
	if !marker || !hint {
		return "", false
	}
	hasAccept, hasDecline := false, false
	for _, l := range labels {
		hasAccept = hasAccept || l == bypassAccept
		hasDecline = hasDecline || l == bypassDecline
	}
	if !hasAccept || !hasDecline {
		return "", false
	}
	var marked []string
	for i, t := range trimmed {
		if strings.HasPrefix(t, "❯") && (labels[i] == bypassAccept || labels[i] == bypassDecline) {
			marked = append(marked, labels[i])
		}
	}
	if len(marked) == 1 {
		return marked[0], true
	}
	return "", true
}

// Claude Code's backgrounded-session picker: the conversation moved to the background and the screen is a list of sessions. Its
// new-session box has the ordinary composer shape, so the shape alone cannot tell it from the agent's own prompt, and text typed
// there goes to a new session. Its hint line does ("enter to open" or "space to reply" at or below the box), but that is ordinary
// English an agent's own transcript quotes above the box, so the picker's banner is required above the box too.
var backgroundedHints = []string{"enter to open", "space to reply"}

const backgroundBanner = "moved to the background"

func hasPickerHint(row string) bool {
	for _, h := range backgroundedHints {
		if strings.Contains(row, h) {
			return true
		}
	}
	return false
}

// pickerBox is (banner top, hint) when the composer's hint carries a picker phrase and the picker's banner is above it.
func pickerBox(tail []string) (top int, ok bool) {
	top, bottom, has := ComposerBox(tail)
	if !has {
		return 0, false
	}
	hint := false
	for i := bottom; i < len(tail); i++ {
		hint = hint || hasPickerHint(tail[i])
	}
	if !hint {
		return 0, false
	}
	for _, x := range tail[:top] {
		if strings.Contains(x, backgroundBanner) {
			return top, true
		}
	}
	return 0, false
}

// hintBelowBox is whether the composer's hint says a picker phrase, banner or not: the unconfirmed match fails closed.
func hintBelowBox(tail []string) bool {
	_, bottom, ok := ComposerBox(tail)
	if !ok {
		return false
	}
	for i := bottom; i < len(tail); i++ {
		if hasPickerHint(tail[i]) {
			return true
		}
	}
	return false
}

// describeBackgrounded is the picker's banner and session list above its own box, quoted with the care of DescribeModal.
func describeBackgrounded(tail []string, top int) string {
	var shown []string
	for _, x := range tail[:top] {
		ln := oneline(x)
		if ln != "" && !composerRuleRE.MatchString(ln) && ln != "❯" {
			shown = append(shown, ln)
		}
	}
	if len(shown) > 3 {
		shown = shown[:3]
	}
	return cut(strings.Join(shown, " / "), DetailChars)
}

func (claude) ScreenBlocker(tail []string) *Blocker {
	if footer := footerRow(tail); footer >= 0 {
		// Deliberately not widened: a long command's title stays "", so autoaccept never matches a standing rule against a
		// dialog it could not read whole.
		b := &Blocker{Kind: Modal, Detail: DescribeModal(tail, footer)}
		if d := ParseDialog(tail, footer, false); d != nil {
			b.Title = d.Title
		}
		return b
	}
	if top, ok := pickerBox(tail); ok {
		return &Blocker{Kind: Backgrounded, Detail: describeBackgrounded(tail, top)}
	}
	if hintBelowBox(tail) {
		return &Blocker{Kind: Unreadable} // shaped like a picker not measured: fail closed
	}
	if ComposerShape(tail) {
		return nil
	}
	return &Blocker{Kind: Unreadable}
}

func (claude) TrustAnswer() string   { return trustPromptDownArrow }
func (claude) BypassAccept() string  { return bypassAccept }
func (claude) BypassNextKey() string { return trustPromptDownArrow }

// Verified against a live prompt: one Down off the default cursor ("No, exit") lands on "Yes, I trust this folder"; a bare Enter
// then confirms it. A bare Enter with no Down confirms "No, exit" instead.
const trustPromptDownArrow = "\x1b[B"

// AcceptsTool: launch passes --no-chrome unless the agent opted in, and an opted-in agent's browser prompt is the founder's.
func (claude) AcceptsTool(string) bool { return false }

func (claude) AcceptingKey(*Dialog, string) (string, string) {
	return "", "this vendor's prompts are not auto-accepted"
}

// Confirmation is Claude Code's one-time permission prompt, reconstructed from its wording, not measured on a live screen: the
// match is as narrow as that wording allows, so a layout that differs is not recognized and stays for the terminal.
func (claude) Confirmation(d *Dialog) *Confirmation {
	if !strings.Contains(d.Footer, "Esc to cancel") {
		return nil
	}
	var prose []string
	for _, ln := range d.Body {
		if !optionLabelRE.MatchString(ln) {
			prose = append(prose, ln)
		}
	}
	var asked []int
	for i, ln := range prose {
		if strings.ToLower(strings.TrimSpace(ln)) == "do you want to proceed?" {
			asked = append(asked, i)
		}
	}
	if len(asked) != 1 || asked[0] < 2 {
		return nil // no question, or nothing above it saying what is asked
	}
	var yes, no []int
	for _, o := range d.Options {
		l := normLabel(o.Label)
		if l == "yes" {
			yes = append(yes, o.Number)
		}
		if strings.SplitN(l, ",", 2)[0] == "no" {
			no = append(no, o.Number)
		}
	}
	if len(yes) != 1 || len(no) != 1 {
		return nil
	}
	return &Confirmation{prose[0], append([]string{}, prose[1:asked[0]]...), yes[0], no[0]}
}

func (claude) AskQuestion(tail []string) *Question { return recognizeAsk(tail) }

func (claude) AnswerQuestionKeys(q *Question, choices []int, text *string) []string {
	return questionKeys(q, choices, text)
}

func (claude) QuestionToggleKeys(q *Question, choices []int) []string {
	return toggleKeysForOther(q, choices)
}

func (claude) QuestionOtherAfterToggleKeys(q *Question, text string) []string {
	return finishOtherKeys(q, text)
}

// ---- opencode: the screen is its only witness

type opencode struct{}

func (opencode) Name() string { return "opencode" }

var boxBottomRE = regexp.MustCompile(`^\s*\x{2579}\x{2580}{8,}`)

const footerRows = 6

// footer is the rows under the prompt box; ok is false when no box is drawn.
func (opencode) footer(tail []string) ([]string, bool) {
	rows := tail
	if len(rows) > footerRows {
		rows = rows[len(rows)-footerRows:]
	}
	for i := len(rows) - 1; i >= 0; i-- {
		if boxBottomRE.MatchString(rows[i]) {
			return rows[i+1:], true
		}
	}
	return nil, false
}

func (o opencode) WorkingOnScreen(tail []string) (bool, bool) {
	footer, ok := o.footer(tail)
	if !ok {
		return false, false
	}
	for _, r := range footer {
		if strings.Contains(r, "interrupt") {
			return true, true
		}
	}
	return false, true
}

func (o opencode) TurnOnScreen(tail []string) bool { w, _ := o.WorkingOnScreen(tail); return w }

// opencode's screen carries no background count.
func (opencode) IdleWithBackground([]string) bool { return false }

// ScreenBlocker: a permission request replaces the prompt box with its choices, the first already selected, so Enter answers it.
// Anything without the box cannot be typed into safely.
func (o opencode) ScreenBlocker(tail []string) *Blocker {
	low := max(0, len(tail)-footerRows)
	ask := -1
	for i := len(tail) - 1; i >= low; i-- {
		if strings.Contains(tail[i], "enter confirm") {
			ask = i
			break
		}
	}
	if ask >= 0 {
		lines := make([]string, ask)
		for i := 0; i < ask; i++ {
			lines[i] = textsafe.Oneline(strings.Trim(tail[i], " ┃"))
		}
		title := -1
		for i := len(lines) - 1; i >= 0; i-- {
			if strings.HasPrefix(lines[i], "△") {
				title = i
				break
			}
		}
		from := lines
		if title >= 0 {
			from = lines[title:]
		} else if len(lines) > 3 {
			from = lines[len(lines)-3:]
		}
		var shown []string
		for _, ln := range from {
			if ln != "" {
				shown = append(shown, ln)
			}
		}
		if len(shown) > 3 {
			shown = shown[:3]
		}
		return &Blocker{Kind: Modal, Detail: cut(strings.Join(shown, " / "), DetailChars)}
	}
	if _, ok := o.footer(tail); !ok {
		return &Blocker{Kind: Unreadable}
	}
	return nil
}

// No folder-trust dialog, bypass warning, permission prompt or AskUserQuestion of its own to detect.
func (opencode) TrustScreen([]string) (string, bool)  { return "", false }
func (opencode) BypassScreen([]string) (string, bool) { return "", false }
func (opencode) TrustAnswer() string                  { return "" }
func (opencode) BypassAccept() string                 { return "" }
func (opencode) BypassNextKey() string                { return "" }
func (opencode) AcceptsTool(string) bool              { return false }
func (opencode) AcceptingKey(*Dialog, string) (string, string) {
	return "", "this vendor's prompts are not auto-accepted"
}
func (opencode) Confirmation(*Dialog) *Confirmation                      { return nil }
func (opencode) AskQuestion([]string) *Question                          { return nil }
func (opencode) AnswerQuestionKeys(*Question, []int, *string) []string   { return nil }
func (opencode) QuestionToggleKeys(*Question, []int) []string            { return nil }
func (opencode) QuestionOtherAfterToggleKeys(*Question, string) []string { return nil }

// ---- claude's AskUserQuestion picker

// parse_dialog cannot read this screen: it takes the rule closest to the footer as the panel's own top, which here is the second
// rule, above only "Chat about this". This walks tail from the footer upward through the fixed shape: footer, blank,
// "N. Chat about this", blank, a rule, the numbered options (each optionally an indented description line below it), the
// question, the header, a rule. A call with more than one question, or a multiSelect question's own "Review your answers" screen,
// is not read: recognizeAsk returns nil rather than guess at either.
const askFooterMarker = "Esc to cancel"

var (
	chatAboutThisRE = regexp.MustCompile(`(?i)^(\d+)\.\s+chat about this$`)
	singleHeaderRE  = regexp.MustCompile(`^☐\s+(.+)$`)
	tabHeaderRE     = regexp.MustCompile(`^←\s+(.+)→$`)
	tabIconRE       = regexp.MustCompile(`^[☐☒✅✔]\s*`)
	askOptionRE     = regexp.MustCompile(`^(❯)?\s*(\d+)\.\s+(?:\[(.)\]\s*)?(\S.*)$`)
	twoSpacesRE     = regexp.MustCompile(`\s{2,}`)
)

const (
	typeSomething = "type something"
	right         = "\x1b[C"
	down          = "\x1b[B"
)

// questionKeys is the keys that answer q, sent in order, or nil when this combination is not sent here. One path per shape:
// single-select a choice: its own number submits outright; single-select Other: its number, the text, Enter; multiSelect
// choices: toggle only what differs from what is checked, then Right onto "Submit", then "1"; multiSelect Other: Down from the
// cursor to Other, the text, one more Down onto "Submit", Enter, then "1" for the Review screen. Boxes and Other together are
// refused: toggling a box moves the cursor too.
func questionKeys(q *Question, choices []int, text *string) []string {
	for _, c := range choices {
		if c < 0 || c >= len(q.Options) {
			return nil
		}
	}
	if text != nil {
		if q.OtherNumber == 0 || len(choices) > 0 {
			return nil
		}
		if !q.MultiSelect {
			return []string{strconv.Itoa(q.OtherNumber), *text, Enter}
		}
		downs := q.OtherNumber - q.Cursor
		if downs < 0 {
			return nil
		}
		return append(repeat(down, downs), *text, down, Enter, "1")
	}
	if !q.MultiSelect {
		if len(choices) == 1 {
			return []string{strconv.Itoa(choices[0] + 1)}
		}
		return nil
	}
	// Empty choices is a real answer on a multiSelect question: nothing picked, so it still submits.
	return append(toggleKeys(q, choices), right, "1")
}

func repeat(s string, n int) []string {
	out := make([]string, n)
	for i := range out {
		out[i] = s
	}
	return out
}

func toggleKeys(q *Question, choices []int) []string {
	desired := map[int]bool{}
	for _, c := range choices {
		desired[c] = true
	}
	var keys []string
	for i, o := range q.Options {
		if desired[i] != o.Checked {
			keys = append(keys, strconv.Itoa(i+1))
		}
	}
	return keys
}

// toggleKeysForOther is stage one of a multiSelect question's boxes-and-Other combo.
func toggleKeysForOther(q *Question, choices []int) []string {
	if !q.MultiSelect {
		return nil
	}
	for _, c := range choices {
		if c < 0 || c >= len(q.Options) {
			return nil
		}
	}
	return toggleKeys(q, choices)
}

// finishOtherKeys is stage two, given q freshly re-read after the toggles landed.
func finishOtherKeys(q *Question, text string) []string {
	if q.OtherNumber == 0 {
		return nil
	}
	downs := q.OtherNumber - q.Cursor
	if downs < 0 {
		return nil
	}
	return append(repeat(down, downs), text, down, Enter, "1")
}

func recognizeAsk(tail []string) *Question {
	footer := -1
	for i := len(tail) - 1; i >= 0; i-- {
		if strings.Contains(tail[i], askFooterMarker) {
			footer = i
			break
		}
	}
	if footer < 0 {
		return nil
	}
	lines := make([]string, footer)
	for i := range lines {
		lines[i] = strings.TrimRight(tail[i], " \t\r\n\f\v")
	}
	i := lastNonblank(lines, len(lines)-1)
	if i < 0 {
		return nil
	}
	chat := chatAboutThisRE.FindStringSubmatch(strings.TrimSpace(lines[i]))
	if chat == nil {
		return nil
	}
	chatNumber, _ := strconv.Atoi(chat[1])
	midRule := lastNonblank(lines, i-1)
	if midRule < 0 || !composerRuleRE.MatchString(strings.TrimSpace(lines[midRule])) {
		return nil
	}
	topRule := -1
	for k := midRule - 1; k >= 0; k-- {
		if composerRuleRE.MatchString(strings.TrimSpace(lines[k])) {
			topRule = k
			break
		}
	}
	if topRule < 0 {
		return nil
	}
	var body []string
	for _, ln := range lines[topRule+1 : midRule] {
		if strings.TrimSpace(ln) != "" {
			body = append(body, ln)
		}
	}
	if len(body) < 3 {
		return nil
	}
	header, multi, ok := askHeader(strings.TrimSpace(body[0]))
	if !ok {
		return nil
	}
	opts, other, otherText, hasOther, cursor := askOptions(body[2:], multi)
	if other == 0 || other+1 != chatNumber {
		return nil
	}
	return &Question{Header: header, Question: strings.TrimSpace(body[1]), Options: opts, MultiSelect: multi, OtherNumber: other,
		OtherText: otherText, HasOtherText: hasOther, Lines: body, Cursor: cursor}
}

func lastNonblank(lines []string, start int) int {
	i := start
	for i >= 0 && strings.TrimSpace(lines[i]) == "" {
		i--
	}
	return i
}

// askHeader is the header line of a lone question: " ☐ <header>" (single-select) or "←  ☒ <header>  ✔ Submit  →" (multiSelect,
// which always has its own "Submit" step even with one question).
func askHeader(line string) (header string, multi, ok bool) {
	if m := singleHeaderRE.FindStringSubmatch(line); m != nil {
		return strings.TrimSpace(m[1]), false, true
	}
	m := tabHeaderRE.FindStringSubmatch(line)
	if m == nil {
		return "", false, false
	}
	var parts []string
	for _, p := range twoSpacesRE.Split(strings.TrimSpace(m[1]), -1) {
		if strings.TrimSpace(p) != "" {
			parts = append(parts, strings.TrimSpace(tabIconRE.ReplaceAllString(p, "")))
		}
	}
	// A real second question adds a third tab before "Submit": not this scan's to read.
	if len(parts) != 2 || strings.ToLower(parts[1]) != "submit" {
		return "", false, false
	}
	return parts[0], true, true
}

func askOptions(lines []string, multi bool) (opts []QuestionOption, other int, otherText string, hasOther bool, cursor int) {
	cursor = 1
	i, n := 0, 1
	for i < len(lines) {
		m := askOptionRE.FindStringSubmatch(strings.TrimSpace(lines[i]))
		if m == nil {
			return nil, 0, "", false, 1
		}
		if num, _ := strconv.Atoi(m[2]); num != n {
			return nil, 0, "", false, 1
		}
		if m[1] != "" {
			cursor = n
		}
		box, label := m[3], strings.TrimSpace(m[4])
		i++
		var desc []string
		for i < len(lines) && askOptionRE.FindStringSubmatch(strings.TrimSpace(lines[i])) == nil {
			text := strings.TrimSpace(lines[i])
			// A multiSelect header's own wide tab bar sometimes wraps its "Submit" tab onto the row under the last option.
			if !(multi && i == len(lines)-1 && text == "Submit") {
				desc = append(desc, text)
			}
			i++
		}
		isLast := i == len(lines)
		switch {
		case strings.ToLower(strings.TrimRight(label, ".")) == typeSomething:
			other = n
		case isLast && other == 0:
			other, otherText, hasOther = n, label, true
		default:
			opts = append(opts, QuestionOption{label, strings.Join(desc, " "), box == "✔" || box == "x" || box == "X"})
		}
		n++
	}
	return opts, other, otherText, hasOther, cursor
}
