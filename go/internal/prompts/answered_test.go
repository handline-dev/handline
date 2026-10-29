package prompts

import (
	"database/sql"
	"testing"
)

// answered is the prompt_answers row of the prompt the caller named: label, note ("" for none) and who answered.
func answered(t *testing.T, e *Env, fingerprint string) (label, note, by string) {
	t.Helper()
	var n sql.NullString
	err := e.DB.QueryRow("SELECT a.label, a.note, a.by_name FROM prompt_answers a JOIN prompt_blocks b ON b.id = a.prompt_id "+
		"WHERE b.fingerprint = ?", fingerprint).Scan(&label, &n, &by)
	if err != nil {
		t.Fatalf("no answer kept for %s: %v", fingerprint, err)
	}
	return label, n.String, by
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
	if e.DB.QueryRow("SELECT count(*) FROM prompt_answers").Scan(&n); n != 0 {
		t.Fatalf("%d answers kept for a refused one", n)
	}
}
