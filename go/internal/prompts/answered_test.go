package prompts

import (
	"database/sql"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/term"
)

// answered is what prompt.answer kept on the block the caller named: label, note and who answered.
func answered(t *testing.T, e *Env, fingerprint string) (label, note, by string) {
	t.Helper()
	var l, n, b sql.NullString
	err := e.DB.QueryRow("SELECT answer_label, answer_note, answer_by FROM prompt_blocks WHERE fingerprint = ?", fingerprint).Scan(&l, &n, &b)
	if err != nil || !l.Valid {
		t.Fatalf("no answer kept for %s: %v", fingerprint, err)
	}
	return l.String, n.String, b.String
}

// What prompt.answer answered is kept for the chat line: a permission's Allowed or Denied with the deny's reason, a question's typed
// text as the note under Other, a multi-pick's labels, and who answered.
func TestAnAnswerIsKeptWithItsWordsAndWhoGaveIt(t *testing.T) {
	e, h := testEnv(t)
	seedAgent(t, e, "backend", "%1")
	register(e)
	h.set("%1", []string{"> working"})
	_, b := ask(t, e, "go vet ./...")
	if _, err := answers(e, b.Fingerprint, "allow"); err != nil {
		t.Fatal(err)
	}
	if label, note, by := answered(t, e, b.Fingerprint); label != "Allowed" || note != "" || by != "casey" {
		t.Fatalf("permission allow: %q %q %q", label, note, by)
	}
	_, b = ask(t, e, "rm -rf build")
	if _, err := e.answer(asRon(), map[string]any{"agent": "backend", "prompt": b.Fingerprint, "choice": "deny", "note": " use the script "}); err != nil {
		t.Fatal(err)
	}
	if label, note, _ := answered(t, e, b.Fingerprint); label != "Denied" || note != "use the script" {
		t.Fatalf("permission deny: %q %q", label, note)
	}

	e, h = testEnv(t)
	id := shown(t, e, h, question)
	clearsOnAnyKey(h)
	if _, err := answers(e, id, map[string]any{"text": "temporal"}); err != nil {
		t.Fatal(err)
	}
	if label, note, _ := answered(t, e, id); label != "Other" || note != "temporal" {
		t.Fatalf("typed text: %q %q", label, note)
	}

	e, h = testEnv(t)
	id = shown(t, e, h, multi)
	clearsOnAnyKey(h)
	if _, err := answers(e, id, []any{"1", "3"}); err != nil {
		t.Fatal(err)
	}
	if label, note, _ := answered(t, e, id); label != "dark mode, offline sync" || note != "" {
		t.Fatalf("multi-pick: %q %q", label, note)
	}
}

// A refused answer keeps nothing: the chat line must not say a prompt got an answer it did not.
func TestARefusedAnswerKeepsNothing(t *testing.T) {
	e, h := testEnv(t)
	id := shown(t, e, h, question)
	h.set("%1", multi) // another prompt is on screen now
	if _, err := answers(e, id, "2"); err == nil {
		t.Fatal("a stale answer was accepted")
	}
	var n int
	if e.DB.QueryRow("SELECT count(*) FROM prompt_blocks WHERE answer_label IS NOT NULL").Scan(&n); n != 0 {
		t.Fatalf("%d answers kept for a refused one", n)
	}
}

// Esc is Denied for a permission and Cancelled for a question; a dialog's pick is its option's label; a question option with no label
// is said by its number.
func TestACancelADialogPickAndAnUnlabelledOptionAreSaidInWords(t *testing.T) {
	e, h := testEnv(t)
	noteWait(t, e, "backend", "rm -rf build", 7)
	id := shown(t, e, h, boxless(t).Screen)
	clearsOnAnyKey(h)
	if _, err := answers(e, id, "esc"); err != nil {
		t.Fatal(err)
	}
	if label, _, _ := answered(t, e, id); label != "Denied" {
		t.Fatalf("permission Esc: %q", label)
	}

	e, h = testEnv(t)
	id = shown(t, e, h, question)
	clearsOnAnyKey(h)
	if _, err := answers(e, id, "esc"); err != nil {
		t.Fatal(err)
	}
	if label, _, _ := answered(t, e, id); label != "Cancelled" {
		t.Fatalf("question Esc: %q", label)
	}

	e, h = testEnv(t)
	var dialog []string
	for _, c := range captures(t) {
		if c.Name == "yes_no_dialog" {
			dialog = c.Screen
		}
	}
	id = shown(t, e, h, dialog)
	clearsOnAnyKey(h)
	if _, err := answers(e, id, "2"); err != nil {
		t.Fatal(err)
	}
	var opts string
	e.DB.QueryRow("SELECT detail FROM prompt_blocks WHERE fingerprint = ?", id).Scan(&opts)
	if label, _, _ := answered(t, e, id); label == "" || label == "2" || !strings.Contains(opts, label) {
		t.Fatalf("dialog pick: %q, options %s", label, opts)
	}

	b2 := &Block{Kind: term.PromptQuestion, Detail: map[string]any{"options": []any{map[string]any{"label": "main"}, map[string]any{}}}}
	if label, _ := answerLabel(b2, "2", Choice{Choices: []int{1}}); label != "2" {
		t.Fatalf("an option with no label: %q", label)
	}
}
