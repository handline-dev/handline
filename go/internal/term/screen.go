package term

import (
	"regexp"
	"strconv"
	"strings"

	"github.com/handline/handline/go/internal/textsafe"
)

// What Claude Code draws under a modal it opens itself ("Teach auto mode about your environment?", trust prompts).
var modalMarkers = []string{"Enter to confirm", "Esc to cancel"}

const (
	modalRows = 3
	// DetailChars is how much of a dialog describeModal quotes.
	DetailChars = 200
	// TruncatedTitle is what ParseDialog shows as the title when a dialog is real but its own top rule and title are still
	// off-screen at the widest read: a stand-in that says so, never a guess dressed up as the real one.
	TruncatedTitle = "(command not fully shown - its start is off-screen)"
	// Escape and CtrlC are the bytes a key press sends. Esc was checked on live Claude Code dialogs that closed on the next read.
	Escape = "\x1b"
	CtrlC  = "\x03"
)

// A dialog's panel opens with a rule; the title is the first line under it. The exit confirmation draws it with U+2594, the
// tool permission prompt with U+2500.
var (
	ruleRE        = regexp.MustCompile(`^[\x{2594}\x{2500}]{8,}$`)
	optionRE      = regexp.MustCompile(`^(?:\x{276f}\s*)?\d+\.\s+\S`)
	optionLabelRE = regexp.MustCompile(`^(?:\x{276f}\s*)?(\d+)\.\s+(\S.*)$`)
	numberedRE    = regexp.MustCompile(`^(?:\x{276f}\s*)?\d+\.\s`)
	escMarkRE     = regexp.MustCompile(`(?i)\(esc\)\s*$`)
	escTailRE     = regexp.MustCompile(`\s*\(esc\)$`)
)

// Option is a numbered choice of a dialog.
type Option struct {
	Number int
	Label  string
}

// Dialog is a confirmation dialog read whole: its title, its numbered options, its footer, and Body, every non-empty line of
// the panel verbatim, so a caller deciding what Esc does can look at the whole panel and not only what this parse made data.
type Dialog struct {
	Title   string
	Options []Option
	Footer  string
	Body    []string
}

func oneline(s string) string { return textsafe.Oneline(strings.TrimSpace(s)) }

func lastRule(lines []string) (int, bool) {
	for i := len(lines) - 1; i >= 0; i-- {
		if ruleRE.MatchString(lines[i]) {
			return i, true
		}
	}
	return 0, false
}

func nonEmpty(lines []string) []string {
	var out []string
	for _, ln := range lines {
		if ln != "" {
			out = append(out, ln)
		}
	}
	return out
}

// numbered is the options among body, or nil unless there are two or more numbered 1, 2, ... in order.
func numbered(body []string) []Option {
	var opts []Option
	for _, ln := range body {
		if m := optionLabelRE.FindStringSubmatch(ln); m != nil {
			n, _ := strconv.Atoi(m[1])
			opts = append(opts, Option{n, strings.TrimSpace(m[2])})
		}
	}
	if len(opts) < 2 {
		return nil
	}
	for i, o := range opts {
		if o.Number != i+1 {
			return nil
		}
	}
	return opts
}

// footerRow is the index of the dialog's footer among the bottom rows, or -1.
func footerRow(tail []string) int {
	for i := max(0, len(tail)-modalRows); i < len(tail); i++ {
		for _, m := range modalMarkers {
			if strings.Contains(tail[i], m) {
				return i
			}
		}
	}
	return -1
}

func cleaned(tail []string, n int) []string {
	out := make([]string, n)
	for i := 0; i < n; i++ {
		out[i] = oneline(tail[i])
	}
	return out
}

// DescribeModal is the dialog above row footer in its own words: its title, then its numbered options. Where no rule marks where
// the dialog begins, the last three lines above the footer, verbatim, so a layout not seen still says something true.
func DescribeModal(tail []string, footer int) string {
	lines := cleaned(tail, footer)
	top, ok := lastRule(lines)
	if !ok {
		var shown []string
		for _, ln := range lines {
			if ln != "" {
				shown = append(shown, ln)
			}
		}
		if len(shown) > 3 {
			shown = shown[len(shown)-3:]
		}
		return cut(strings.Join(shown, " / "), DetailChars)
	}
	body := nonEmpty(lines[top+1:])
	var opts []string
	for _, ln := range body {
		if optionRE.MatchString(ln) {
			opts = append(opts, strings.TrimSpace(strings.TrimPrefix(ln, "❯")))
		}
	}
	text := ""
	if len(body) > 0 {
		text = `"` + body[0] + `"`
	}
	if len(opts) > 0 {
		text += " - " + strings.Join(opts, " / ")
	}
	return cut(text, DetailChars)
}

// cut is s cut to n characters.
func cut(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[:n])
	}
	return s
}

// ParseDialog is the dialog above row footer as data, or nil when any part of it is not there to read: no rule opening the
// panel, no title, or fewer than two options numbered 1, 2, ... in order. What cannot be parsed is not guessed. allowTruncated is
// the last resort once widening the read finds no rule: every visible line becomes the body and TruncatedTitle stands in.
func ParseDialog(tail []string, footer int, allowTruncated bool) *Dialog {
	lines := cleaned(tail, footer)
	top, ok := lastRule(lines)
	if !ok {
		if !allowTruncated {
			return nil
		}
		body := nonEmpty(lines)
		opts := numbered(body)
		if opts == nil {
			return nil
		}
		return &Dialog{TruncatedTitle, opts, oneline(tail[footer]), body}
	}
	body := nonEmpty(lines[top+1:])
	if len(body) == 0 || optionLabelRE.MatchString(body[0]) {
		return nil
	}
	opts := numbered(body[1:])
	if opts == nil {
		return nil
	}
	return &Dialog{body[0], opts, oneline(tail[footer]), body}
}

// The whole label of an option that leaves the world as it is, and nothing wider: 'No, exit' and 'No, and tell Claude what to do
// differently' are choices, and are not in it.
var inertLabels = map[string]bool{"stay": true, "cancel": true, "no": true, "back": true, "keep": true, "do nothing": true,
	"not now": true, "never mind": true}

func normLabel(label string) string {
	s := escTailRE.ReplaceAllString(strings.ToLower(strings.TrimSpace(label)), "")
	return strings.TrimRight(s, ".!")
}

func inert(label string) bool { return inertLabels[normLabel(label)] }

// usageLimitDialog is what Claude Code asks when a turn ends on the session limit: none of its options only declines, but Esc
// was measured on a live one (2026-09-21): the dialog closed and nothing was chosen. Named by the two options that make it this
// dialog, by their start only, since the second carries the reset time.
func usageLimitDialog(d *Dialog) bool {
	a, b := false, false
	for _, o := range d.Options {
		a = a || strings.HasPrefix(o.Label, "Stop and wait for limit to reset")
		b = b || strings.HasPrefix(o.Label, "Wait here, then continue automatically")
	}
	return a && b
}

// DecliningKey is Esc, when the dialog itself says Esc cancels it and offers an option that only declines, or is the usage-limit
// dialog whose Esc was measured; otherwise no key and the reason. Never a key that chooses.
//
// An option marked "(esc)" is what Esc does, read off the screen: the trust dialog's 'No, exit (esc)' is not inert. The mark is
// looked for across the whole panel, so one in a title or a free-text line refuses too.
func DecliningKey(d *Dialog) (string, string) {
	if !strings.Contains(d.Footer, "Enter to confirm") || !strings.Contains(d.Footer, "Esc to cancel") {
		return "", "its footer does not say Enter confirms and Esc cancels"
	}
	for _, ln := range d.Body {
		if escMarkRE.MatchString(ln) && !optionLabelRE.MatchString(ln) {
			return "", "the (esc) mark appears where it cannot be evaluated: " + pyRepr(ln)
		}
	}
	var marked []string
	for _, o := range d.Options {
		if escMarkRE.MatchString(o.Label) {
			marked = append(marked, o.Label)
		}
	}
	if len(marked) > 0 {
		if len(marked) > 1 {
			return "", "more than one option is marked (esc)"
		}
		if !inert(marked[0]) {
			return "", "the option marked (esc) is not one that only declines: " + pyRepr(marked[0])
		}
		return Escape, ""
	}
	if usageLimitDialog(d) {
		return Escape, ""
	}
	for _, o := range d.Options {
		if inert(o.Label) {
			return Escape, ""
		}
	}
	return "", "none of its options only declines"
}

const (
	exitOverWorkTitle = "background work is running"
	exitAndStopLabel  = "exit and stop tasks"
	// Enter is the key name for a bare Enter, as the readers' pickers return it.
	Enter = "enter"
)

// highlighted is the dialog's body line marked as the highlighted option, when exactly one is.
func highlighted(d *Dialog) []string {
	var marked []string
	for _, ln := range d.Body {
		if strings.HasPrefix(ln, "❯") {
			marked = append(marked, ln)
		}
	}
	if len(marked) != 1 {
		return nil
	}
	return optionLabelRE.FindStringSubmatch(marked[0])
}

// StoppingKey is Enter, when this is the exit dialog over background work and the highlighted option is the one that stops the
// tasks and exits. The one place the daemon chooses to stop a session's work, so as narrow as the parse allows.
func StoppingKey(d *Dialog) (string, string) {
	if strings.ToLower(strings.TrimSpace(d.Title)) != exitOverWorkTitle {
		return "", "it is not the exit dialog over background work"
	}
	if !strings.Contains(d.Footer, "Enter to confirm") {
		return "", "its footer does not say Enter confirms"
	}
	m := highlighted(d)
	if m == nil || normLabel(m[2]) != exitAndStopLabel {
		return "", "its highlighted option is not 'Exit and stop tasks'"
	}
	return Enter, ""
}

// highlightedNumber is the number of the one option marked as highlighted, or 0.
func highlightedNumber(d *Dialog) int {
	if m := highlighted(d); m != nil {
		n, _ := strconv.Atoi(m[1])
		return n
	}
	return 0
}

// digitKey is the one key that presses option number of d: a digit, and only for an option the dialog shows.
func digitKey(d *Dialog, number int) (string, string) {
	if number < 1 || number > 9 { // 10 would be sent as the keys "1", "0": the dialog takes the 1
		return "", "option " + strconv.Itoa(number) + " cannot be pressed by one key"
	}
	for _, o := range d.Options {
		if o.Number == number {
			return strconv.Itoa(number), ""
		}
	}
	return "", "the dialog has no option " + strconv.Itoa(number)
}

// Context is the panel's own lines the modal does not already show as title or option buttons.
func Context(d *Dialog) []string {
	shown := map[string]bool{strings.TrimSpace(d.Title): true}
	for _, o := range d.Options {
		shown[strings.TrimSpace(o.Label)] = true
	}
	out := []string{}
	for _, ln := range d.Body {
		t := strings.TrimSpace(ln)
		if !shown[t] && !numberedRE.MatchString(t) {
			out = append(out, textsafe.Oneline(ln))
		}
	}
	return out
}
