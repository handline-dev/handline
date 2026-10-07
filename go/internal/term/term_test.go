package term

import (
	"fmt"
	"reflect"
	"strings"
	"sync"
	"testing"
)

var rule = strings.Repeat("─", 60)

// The AskUserQuestion captures the founder made on 2026-09-27/28 (tests/session/test_ask_question.py).
var (
	singleDefault = []string{rule, " ☐ Library", "", "Which library should we use for dates?", "",
		"❯ 1. date-fns", "     small, tree-shakeable", "  2. dayjs", "     momentjs-compatible API",
		"  3. luxon", "     built-in timezone support", "  4. Type something.",
		rule, "  5. Chat about this", "", "Enter to select · ↑/↓ to navigate · Esc to cancel"}
	singleOther = []string{rule, " ☐ Library", "", "Which library should we use for dates?", "",
		"  1. date-fns", "     small, tree-shakeable", "  2. dayjs", "     momentjs-compatible API",
		"  3. luxon", "     built-in timezone support", "❯ 4. hihi",
		rule, "  5. Chat about this", "", "Enter to select · ↑/↓ to navigate · ctrl+g to edit in VS Code · Esc to cancel"}
	multiSelect = []string{rule, "←  ☒ Features  ✔ Submit  →", "", "Which features should we enable?", "",
		"  1. [✔] dark mode", "         system-wide dark theme", "  2. [ ] notifications", "         push alerts",
		"  3. [✔] offline sync", "         works without network", "❯ 4. [ ] Type something", "     Submit",
		rule, "  5. Chat about this", "", "Enter to select · ↑/↓ to navigate · ctrl+g to edit in VS Code · Esc to cancel"}
	multiCursorAtTop = []string{rule, "←  ☐ Needed  ✔ Submit  →", "", "Which features should we enable?", "",
		"❯ 1. [ ] logging", "         request/response logs", "  2. [ ] metrics", "         latency, error rate",
		"  3. [ ] alerts", "         notify on anomaly", "  4. [ ] user admin", "         roles/accounts screen",
		"  5. [ ] Type something", "     Submit", rule, "  6. Chat about this", "",
		"Enter to select · ↑/↓ to navigate · ctrl+g to edit in VS Code · Esc to cancel"}
	multiAfterToggling = []string{rule, "←  ☐ Needed  ✔ Submit  →", "", "Which features should we enable?", "",
		"  1. [✔] logging", "         request/response logs", "  2. [ ] metrics", "         latency, error rate",
		"❯ 3. [✔] alerts", "         notify on anomaly", "  4. [ ] user admin", "         roles/accounts screen",
		"  5. [ ] Type something", "     Submit", rule, "  6. Chat about this", "",
		"Enter to select · ↑/↓ to navigate · ctrl+g to edit in VS Code · Esc to cancel"}
	permissionPrompt = []string{"earlier output", rule, "   Bash command", "     rm -rf build",
		"   Do you want to proceed?", "   ❯ 1. Yes", "     2. No", "   Esc to cancel · Tab to amend"}
	cleared = []string{"back at the prompt"}
)

func strp(s string) *string { return &s }

func TestTheAskUserQuestionPromptIsRecognizedOffTheRealScreen(t *testing.T) {
	d := claude{}
	q := d.AskQuestion(singleDefault)
	if q == nil || q.Header != "Library" || q.Question != "Which library should we use for dates?" || len(q.Options) != 3 ||
		q.Options[0].Label != "date-fns" || q.Options[0].Description != "small, tree-shakeable" || q.MultiSelect || q.OtherNumber != 4 || q.HasOtherText {
		t.Fatalf("%+v", q)
	}
	if q2 := d.AskQuestion(singleOther); q2 == nil || q2.OtherText != "hihi" || !q2.HasOtherText || len(q2.Options) != 3 {
		t.Fatalf("text typed over 'Type something.' is other_text: %+v", q2)
	}
	q3 := d.AskQuestion(multiSelect)
	if q3 == nil || !q3.MultiSelect || q3.Header != "Features" || !q3.Options[0].Checked || q3.Options[1].Checked || !q3.Options[2].Checked ||
		q3.Cursor != 4 || q3.OtherNumber != 4 {
		t.Fatalf("%+v", q3)
	}
	if d.AskQuestion(permissionPrompt) != nil {
		t.Fatal("a plain permission prompt is not a question")
	}
	if (opencode{}).AskQuestion(singleDefault) != nil {
		t.Fatal("a vendor with no such prompt has none")
	}
}

func TestQuestionKeysMatchTheMeasuredSequence(t *testing.T) {
	q := claude{}.AskQuestion(singleDefault)
	m := claude{}.AskQuestion(multiSelect)
	top := claude{}.AskQuestion(multiCursorAtTop)
	cases := []struct {
		name string
		got  []string
		want []string
	}{
		{"a lone single-select question submits on its own number", questionKeys(q, []int{1}, nil), []string{"2"}},
		{"Other types the words and presses Enter", questionKeys(q, nil, strp("my-lib")), []string{"4", "my-lib", Enter}},
		{"an out-of-range choice is refused", questionKeys(q, []int{9}, nil), nil},
		{"an option already checked and still wanted is not pressed", questionKeys(m, []int{0, 2}, nil), []string{right, "1"}},
		{"only the options that must change are toggled", questionKeys(m, []int{1}, nil), []string{"1", "2", "3", right, "1"}},
		{"nothing checked on a multiSelect question still submits", questionKeys(m, []int{}, nil), []string{"1", "3", right, "1"}},
		{"nothing at all on a single-select question is refused", questionKeys(q, []int{}, nil), nil},
		{"Other on a multiSelect question, cursor already there", questionKeys(m, nil, strp("anything")), []string{"anything", down, Enter, "1"}},
		{"Other alongside checked-box changes is refused", questionKeys(m, []int{1}, strp("anything")), nil},
		{"Other reached by Down from wherever the cursor is", questionKeys(top, nil, strp("a new one")), []string{down, down, down, down, "a new one", down, Enter, "1"}},
	}
	for _, c := range cases {
		if !reflect.DeepEqual(c.got, c.want) {
			t.Errorf("%s: got %q, want %q", c.name, c.got, c.want)
		}
	}
	if top.Cursor != 1 || top.OtherNumber != 5 {
		t.Fatalf("%+v", top)
	}
}

// fakeHost shows a screen until a key is sent; screenAt says what it shows once n keys have been sent.
type fakeHost struct {
	mu       sync.Mutex
	sent     []string
	screenAt func(n int) []string
}

func (h *fakeHost) Name() string                                   { return "fake" }
func (h *fakeHost) Available() bool                                { return true }
func (h *fakeHost) ListTerminals() map[string]bool                 { return nil }
func (h *fakeHost) WaitIdle(string, int, func([]string) bool) bool { return true }
func (h *fakeHost) ReadScreen(string, int) *Screen {
	h.mu.Lock()
	defer h.mu.Unlock()
	return &Screen{Tail: h.screenAt(len(h.sent)), Source: "screen"}
}
func (h *fakeHost) Send(_, text string, enter bool) (bool, string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.sent = append(h.sent, fmt.Sprintf("%s/%v", text, enter))
	return true, ""
}

func newTerm(h *fakeHost) *Term {
	t := New(h, nil)
	t.Settle, t.Gap = 0, 0
	return t
}

func TestAMultiSelectAnswerWithBoxesAndOtherIsSentInTwoStagesWithAReadBetween(t *testing.T) {
	// two toggles, then the screen the toggles left, then Other's own keys, then the card gone
	h := &fakeHost{screenAt: func(n int) []string {
		switch {
		case n < 2:
			return multiCursorAtTop
		case n == 2:
			return multiAfterToggling
		}
		return cleared
	}}
	tm := newTerm(h)
	q := claude{}.AskQuestion(multiCursorAtTop)
	outcome, _, why := tm.AnswerQuestion("%1", q.Lines, []int{0, 2}, strp("extra"))
	// cursor now 3, Other is 5: two Downs, the text, one Down onto Submit, Enter, then 1
	want := []string{"1/false", "3/false", down + "/false", down + "/false", "extra/false", down + "/false", "/true", "1/false"}
	if outcome != Cleared || !reflect.DeepEqual(h.sent, want) {
		t.Fatalf("%s %s sent %q", outcome, why, h.sent)
	}
}

func TestAQuestionThatIsNotTheOneAnsweredIsNotPressed(t *testing.T) {
	h := &fakeHost{screenAt: func(int) []string { return singleDefault }}
	outcome, _, why := newTerm(h).AnswerQuestion("%1", []string{"another question"}, []int{0}, nil)
	if outcome != NotPressed || !strings.Contains(why, "not the one that was answered") || len(h.sent) != 0 {
		t.Fatalf("%s %s %q", outcome, why, h.sent)
	}
}

func TestPromptOnScreenRanksPermissionOverQuestionOverDialog(t *testing.T) {
	tm := newTerm(&fakeHost{screenAt: func(int) []string { return permissionPrompt }})
	if kind, c, _, _ := tm.PromptOnScreen("%1"); kind != PromptPermission || c == nil || c.Yes != 1 || c.No != 2 || c.Title != "Bash command" {
		t.Fatalf("%s %+v", kind, c)
	}
	tm = newTerm(&fakeHost{screenAt: func(int) []string { return singleDefault }})
	if kind, _, q, _ := tm.PromptOnScreen("%1"); kind != PromptQuestion || q == nil {
		t.Fatalf("%s", kind)
	}
	tm = newTerm(&fakeHost{screenAt: func(int) []string { return []string{"nothing here", "❯"} }})
	if kind, _, _, _ := tm.PromptOnScreen("%1"); kind != PromptNone {
		t.Fatalf("%s", kind)
	}
}

func TestTheDecliningKeyIsEscOnlyWhereTheScreenSaysItOnlyDeclines(t *testing.T) {
	dlg := func(options []Option, footer string, body ...string) *Dialog {
		return &Dialog{Title: "t", Options: options, Footer: footer, Body: append([]string{"t"}, body...)}
	}
	const both = "Enter to confirm · Esc to cancel"
	cases := []struct {
		name string
		d    *Dialog
		key  string
	}{
		{"an inert option and the footer: Esc", dlg([]Option{{1, "Yes"}, {2, "No"}}, both, "1. Yes", "2. No"), Escape},
		{"'No, exit' is a choice: no key", dlg([]Option{{1, "Yes, I trust this folder"}, {2, "No, exit"}}, both, "1. Yes, I trust this folder", "2. No, exit"), ""},
		{"an (esc) mark on an option that is not inert: no key", dlg([]Option{{1, "Yes"}, {2, "No, exit (esc)"}}, both, "1. Yes", "2. No, exit (esc)"), ""},
		{"an (esc) mark in free text: no key", dlg([]Option{{1, "Yes"}, {2, "No"}}, both, "Press Esc to exit (esc)", "1. Yes", "2. No"), ""},
		{"a footer that does not say Esc cancels: no key", dlg([]Option{{1, "Yes"}, {2, "No"}}, "Enter to confirm"), ""},
		{"no option that only declines: no key", dlg([]Option{{1, "Delete"}, {2, "Overwrite"}}, both), ""},
		{"the usage-limit dialog, whose Esc was measured", dlg([]Option{{1, "Stop and wait for limit to reset"}, {2, "Wait here, then continue automatically"}}, both), Escape},
	}
	for _, c := range cases {
		if key, why := DecliningKey(c.d); key != c.key {
			t.Errorf("%s: got %q (%s)", c.name, key, why)
		}
	}
}

func TestDialogParsingTakesTheRuleAboveTheFooterAndNeverGuesses(t *testing.T) {
	scr := []string{"output", "▔▔▔▔▔▔▔▔▔▔▔▔", "  Overwrite?", "  ❯ 1. Yes", "    2. No", "  Enter to confirm · Esc to cancel"}
	d := ParseDialog(scr, footerRow(scr), false)
	if d == nil || d.Title != "Overwrite?" || len(d.Options) != 2 || d.Options[1].Label != "No" {
		t.Fatalf("%+v", d)
	}
	noRule := []string{"  1. Yes", "  2. No", "  Enter to confirm · Esc to cancel"}
	if ParseDialog(noRule, 2, false) != nil {
		t.Fatal("no rule opening the panel is no dialog")
	}
	if got := ParseDialog(noRule, 2, true); got == nil || got.Title != TruncatedTitle {
		t.Fatalf("a truncated read says so: %+v", got)
	}
	skipped := []string{"▔▔▔▔▔▔▔▔▔▔▔▔", "  T", "  1. Yes", "  3. No", "  Enter to confirm · Esc to cancel"}
	if ParseDialog(skipped, 4, false) != nil {
		t.Fatal("options not numbered 1, 2, ... in order are not read")
	}
}

func TestPermissionFieldsAreOnlyWhatTheScreenStates(t *testing.T) {
	if got := PermissionFields("Edit file", []string{"src/a.py", "│ a reason"}); got != (Fields{}) {
		t.Fatalf("an edit approval's title was never captured: %+v", got)
	}
	if got := PermissionFields("Bash command", nil); got != (Fields{Tool: "Bash"}) {
		t.Fatalf("%+v", got)
	}
	if got := PermissionFields("Bash command", []string{"│ ls", "│ -la"}); got != (Fields{"Bash", "ls\n-la", ""}) {
		t.Fatalf("a boxed command alone has no reason: %+v", got)
	}
	if DialogKind([]string{"Yes", "No"}) != "dialog" || DialogKind([]string{"Exit now", "Stay"}) != "exit_confirm" {
		t.Fatal("a dialog is an exit confirmation by an option that exits")
	}
}

func TestComposerAndShellReadings(t *testing.T) {
	box := []string{rule, "❯ draft text here", rule, "  ⏵⏵ auto mode on · 2 monitors · ↓ to manage"}
	if txt, ok := ComposerText(box); !ok || txt != "drafttexthere" {
		t.Fatalf("%q %v", txt, ok)
	}
	if bg, ok := FooterBackground(box); !ok || bg != "2 monitors" {
		t.Fatalf("%q %v", bg, ok)
	}
	if ShellPromptIn(box) {
		t.Fatal("a box with a hint under it is a session")
	}
	if !ShellPromptIn(append(append([]string{}, box[:3]...), "me@host ~ %")) {
		t.Fatal("a prompt under the box is a shell")
	}
	if !IsTextTyped("hellothere", "hello there") || IsTextTyped("hellothere", "something else") {
		t.Fatal("the composer holds what was typed, whitespace aside")
	}
	if !(claude{}).IdleWithBackground(box) || (claude{}).IdleWithBackground(append(append([]string{}, box...), "esc to interrupt")) {
		t.Fatal("idle with background work is the count without a turn drawn")
	}
}

func TestTheFolderTrustScreenNamesItsFolderAndIsNotAPrompt(t *testing.T) {
	scr := []string{"Quick safety check: Is this a project you created or one you trust?", "", " Accessing workspace:", " /Users/me/pro", "ject", "",
		"❯ 1. Yes, I trust this folder", "  2. No, exit"}
	if folder, shown := (claude{}).TrustScreen(scr); !shown || folder != "/Users/me/project" {
		t.Fatalf("%q %v", folder, shown)
	}
	if _, shown := (claude{}).TrustScreen(singleDefault); shown {
		t.Fatal("not the trust screen")
	}
	tm := newTerm(&fakeHost{screenAt: func(int) []string { return scr }})
	if kind, _, _, _ := tm.PromptOnScreen("%1"); kind != PromptNone {
		t.Fatalf("the trust screen has its own path and reads as none, got %s", kind)
	}
}

// A vendor whose digit only moves the highlight: the dialog stays, the chosen option now carries the cursor and the footer says
// Enter confirms. Enter is then pressed once, and only on that screen.
func TestADigitThatOnlyMovesTheHighlightIsFollowedByOneEnterOnAFreshRead(t *testing.T) {
	before := []string{"earlier output", rule, "   Bash command", "     rm -rf build", "   Do you want to proceed?",
		"     1. Yes", "   ❯ 2. No", "   Enter to confirm · Esc to cancel"}
	moved := []string{"earlier output", rule, "   Bash command", "     rm -rf build", "   Do you want to proceed?",
		"   ❯ 1. Yes", "     2. No", "   Enter to confirm · Esc to cancel"}
	h := &fakeHost{screenAt: func(n int) []string {
		switch n {
		case 0:
			return before
		case 1:
			return moved
		}
		return cleared
	}}
	seen := newTerm(&fakeHost{screenAt: func(int) []string { return before }}).ConfirmationOnScreen("%1")
	if seen == nil {
		t.Fatal("the permission prompt is not recognized")
	}
	outcome, _, why := newTerm(h).AnswerConfirmation("%1", true, seen.Lines, nil)
	if outcome != Cleared || len(h.sent) != 2 || h.sent[0] != "1/false" || h.sent[1] != "/true" {
		t.Fatalf("%s %q %q", outcome, why, h.sent)
	}

	// the footer does not say Enter confirms: the digit is not followed by Enter
	noFooter := append(append([]string{}, moved[:7]...), "   Esc to cancel")
	h = &fakeHost{screenAt: func(n int) []string {
		if n == 0 {
			return before
		}
		return noFooter
	}}
	if outcome, _, _ := newTerm(h).AnswerConfirmation("%1", true, seen.Lines, nil); outcome == Cleared || len(h.sent) != 1 {
		t.Fatalf("%s %q", outcome, h.sent)
	}
}
