package prompts

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/lock"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/structured"
	"github.com/handline/handline/go/internal/term"
)

// The prompt.answer handler against the fake host: the caller named "casey", the agent "backend".
var (
	ruleLine = strings.Repeat("─", 60)
	// The AskUserQuestion capture of 2026-09-27 (internal/term tests): single choice, with a "Type something." row (4).
	question = []string{ruleLine, " ☐ Library", "", "Which library should we use for dates?", "",
		"❯ 1. date-fns", "     small, tree-shakeable", "  2. dayjs", "     momentjs-compatible API",
		"  3. luxon", "     built-in timezone support", "  4. Type something.",
		ruleLine, "  5. Chat about this", "", "Enter to select · ↑/↓ to navigate · Esc to cancel"}
	multi = []string{ruleLine, "←  ☒ Features  ✔ Submit  →", "", "Which features should we enable?", "",
		"  1. [✔] dark mode", "         system-wide dark theme", "  2. [ ] notifications", "         push alerts",
		"  3. [✔] offline sync", "         works without network", "❯ 4. [ ] Type something", "     Submit",
		ruleLine, "  5. Chat about this", "", "Enter to select · ↑/↓ to navigate · ctrl+g to edit in VS Code · Esc to cancel"}
)

func asRon() *rpc.Conn {
	return &rpc.Conn{Caller: rpc.Caller{Role: "member", Info: map[string]any{"kind": "person", "name": "casey"}}}
}

func register(e *Env) {
	dir := paths.AgentsDir(e.Home)
	os.MkdirAll(filepath.Join(dir, "backend"), 0o755)
	os.WriteFile(filepath.Join(dir, "backend", "agent.yaml"), []byte("vendor: claude\n"), 0o644)
}

// shown puts screen on backend's terminal, lets the watch see it and returns the prompt id the caller would name.
func shown(t *testing.T, e *Env, h *host, screen []string) string {
	t.Helper()
	seedAgent(t, e, "backend", "%1")
	register(e)
	h.set("%1", screen)
	if _, err := e.WatchOnce(t.Logf); err != nil {
		t.Fatal(err)
	}
	b, err := e.OpenBlockOf("backend")
	if err != nil || b == nil {
		t.Fatalf("no prompt shown: %v", err)
	}
	return b.Fingerprint
}

func answers(e *Env, prompt string, choice any) (any, error) {
	return e.answer(asRon(), map[string]any{"agent": "backend", "prompt": prompt, "choice": choice})
}

// clearsOnAnyKey is a terminal whose prompt goes away at the first key.
func clearsOnAnyKey(h *host) {
	h.onSend = func(h *host, handle, text string, enter bool) { h.set(handle, []string{"back at the prompt"}) }
}

func refusal(t *testing.T, err error, code string) *rpc.Error {
	t.Helper()
	var re *rpc.Error
	if !errors.As(err, &re) || re.Code != code {
		t.Fatalf("want %s, got %v", code, err)
	}
	return re
}

func TestAPermissionPromptIsAnsweredThroughTheHandler(t *testing.T) {
	e, h := testEnv(t)
	noteWait(t, e, "backend", "rm -rf build", 7)
	id := shown(t, e, h, boxless(t).Screen)
	clearsOnAnyKey(h)
	got, err := answers(e, id, "yes")
	if err != nil || got.(map[string]any)["said"] != "backend: answered Yes" || got.(map[string]any)["accepted"] != true || h.sent[0] != `"1"/false` {
		t.Fatalf("%v %v %v", got, err, h.sent)
	}
	var ok int
	e.DB.QueryRow("SELECT answer_ok FROM prompt_blocks WHERE fingerprint = ?", id).Scan(&ok)
	if ok != 1 {
		t.Fatal("the answer is recorded")
	}
}

func TestAQuestionIsAnsweredByItsOptionKeyAndAFreeTextOnlyWhereThereIsAnOtherRow(t *testing.T) {
	e, h := testEnv(t)
	id := shown(t, e, h, question)
	clearsOnAnyKey(h)
	got, err := answers(e, id, "2")
	if err != nil || !strings.HasPrefix(got.(map[string]any)["said"].(string), "backend: answered") || len(h.sent) == 0 {
		t.Fatalf("%v %v %v", got, err, h.sent)
	}

	e, h = testEnv(t)
	id = shown(t, e, h, question)
	clearsOnAnyKey(h)
	if _, err := answers(e, id, map[string]any{"text": "hihi"}); err != nil || !strings.Contains(strings.Join(h.sent, " "), "hihi") {
		t.Fatalf("a text answer types the text: %v %v", err, h.sent)
	}
	for _, bad := range []any{map[string]any{"text": ""}, map[string]any{"text": "x", "more": 1}, map[string]any{"text": 3}} {
		h.sent = nil
		refusal(t, mustErr(answers(e, id, bad)), "bad_request")
		if len(h.sent) != 0 {
			t.Fatalf("a malformed text answer sent %v", h.sent)
		}
	}
}

func mustErr(_ any, err error) error { return err }

func TestADialogIsAnsweredByItsNumberAndTakesNoText(t *testing.T) {
	e, h := testEnv(t)
	var dlg capture
	for _, c := range captures(t) {
		if c.Name == "yes_no_dialog" {
			dlg = c
		}
	}
	id := shown(t, e, h, dlg.Screen)
	refusal(t, mustErr(answers(e, id, map[string]any{"text": "free"})), "bad_request")
	clearsOnAnyKey(h)
	got, err := answers(e, id, "2")
	if err != nil || !strings.Contains(got.(map[string]any)["said"].(string), "chose 2. No") || h.sent[0] != `"2"/false` {
		t.Fatalf("%v %v %v", got, err, h.sent)
	}
}

func TestAStalePromptGetsTheCurrentOneAndNothingIsSent(t *testing.T) {
	e, h := testEnv(t)
	id := shown(t, e, h, question)
	h.set("%1", multi)
	e.WatchOnce(t.Logf)
	re := refusal(t, mustErr(answers(e, id, "1")), "stale_prompt")
	if re.Data["current"] == nil || len(h.sent) != 0 {
		t.Fatalf("%v %v", re.Data, h.sent)
	}
	h.set("%1", []string{"just output"})
	e.WatchOnce(t.Logf)
	re = refusal(t, mustErr(answers(e, id, "1")), "stale_prompt")
	if re.Data["current"] != nil || len(h.sent) != 0 {
		t.Fatalf("with no prompt open there is no current one: %v", re.Data)
	}
}

func TestAKeyThePromptDoesNotOfferAndAnArrayOnASinglePromptAreBadRequests(t *testing.T) {
	e, h := testEnv(t)
	id := shown(t, e, h, question)
	for _, choice := range []any{"9", "yes", "", []any{"1", "2"}, []any{"1", "1"}, 7, []any{1}} {
		refusal(t, mustErr(answers(e, id, choice)), "bad_request")
	}
	if len(h.sent) != 0 {
		t.Fatalf("sent %v", h.sent)
	}
	_, err := e.answer(asRon(), map[string]any{"agent": "backend", "choice": "1"})
	refusal(t, err, "bad_request")
	_, err = e.answer(asRon(), map[string]any{"agent": "nobody", "prompt": id, "choice": "1"})
	refusal(t, err, "not_found")

	e, h = testEnv(t)
	id = shown(t, e, h, multi)
	clearsOnAnyKey(h)
	if _, err := answers(e, id, []any{"1", "3"}); err != nil {
		t.Fatalf("an array on a multi prompt: %v", err)
	}
	for _, choice := range []any{[]any{"1", "1"}, []any{"6"}} {
		refusal(t, mustErr(answers(e, id, choice)), "bad_request")
	}
}

func TestEscCancelsAPermissionPromptAndAQuestionAndSaysSoWhenItStaysOnScreen(t *testing.T) {
	e, h := testEnv(t)
	noteWait(t, e, "backend", "rm -rf build", 7)
	id := shown(t, e, h, boxless(t).Screen)
	h.onSend = func(h *host, handle, text string, enter bool) { h.set(handle, []string{"gone"}) }
	got, err := answers(e, id, "esc")
	if err != nil || got.(map[string]any)["said"] != "backend: answered No (Esc)" || h.sent[0] != `"\x1b"/false` {
		t.Fatalf("%v %v %v", got, err, h.sent)
	}

	e, h = testEnv(t)
	id = shown(t, e, h, question)
	h.onSend = func(h *host, handle, text string, enter bool) { h.set(handle, []string{"gone"}) }
	if got, err := answers(e, id, "esc"); err != nil || got.(map[string]any)["said"] != "backend: cancelled (Esc)" {
		t.Fatalf("%v %v", got, err)
	}

	e, h = testEnv(t)
	id = shown(t, e, h, question)
	h.sent = nil
	_, err = answers(e, id, "esc")
	re := refusal(t, err, "refused")
	if !strings.Contains(re.Message, "still on screen after Esc") || len(h.sent) != 1 {
		t.Fatalf("%q %v", re.Message, h.sent)
	}
}

func TestSomeoneTypingAtTheAgentRefusesAtOnceAndSendsNothing(t *testing.T) {
	e, h := testEnv(t)
	id := shown(t, e, h, question)
	hold := lock.TakeTerminal(e.Home, "backend")
	if hold == nil {
		t.Fatal("no hold")
	}
	defer hold.Release()
	re := refusal(t, mustErr(answers(e, id, "1")), "refused")
	if re.Data["gate"] != "terminal_lock" || len(h.sent) != 0 {
		t.Fatalf("%v %v", re.Data, h.sent)
	}
}

func holdInput(t *testing.T, e *Env, name string, lastTyped time.Time) {
	t.Helper()
	if _, err := e.DB.Exec("INSERT INTO input_holders(agent, kind, name, since, last_typed) VALUES('backend', 'person', ?, ?, ?)",
		name, lastTyped.Unix(), lastTyped.Unix()); err != nil {
		t.Fatal(err)
	}
}

func lastTyped(e *Env) (n int64) {
	e.DB.QueryRow("SELECT last_typed FROM input_holders WHERE agent = 'backend'").Scan(&n)
	return
}

func TestTheInputHolderGate(t *testing.T) {
	t.Run("held by another: refused and nothing sent", func(t *testing.T) {
		e, h := testEnv(t)
		id := shown(t, e, h, question)
		holdInput(t, e, "someone.else", time.Now())
		re := refusal(t, mustErr(answers(e, id, "1")), "refused")
		if re.Data["gate"] != "input_holder" || !strings.Contains(re.Message, "held by someone.else") || len(h.sent) != 0 {
			t.Fatalf("%v %v", re.Data, h.sent)
		}
	})
	t.Run("held by the caller: answered and touched", func(t *testing.T) {
		e, h := testEnv(t)
		id := shown(t, e, h, question)
		held := time.Now().Add(-5 * time.Minute)
		holdInput(t, e, "casey", held)
		clearsOnAnyKey(h)
		if _, err := answers(e, id, "1"); err != nil {
			t.Fatal(err)
		}
		if got := lastTyped(e); got < time.Now().Add(-time.Minute).Unix() {
			t.Fatalf("last_typed %d was not touched", got)
		}
	})
	t.Run("an idle hold is dropped and the answer goes through", func(t *testing.T) {
		e, h := testEnv(t)
		id := shown(t, e, h, question)
		holdInput(t, e, "someone.else", time.Now().Add(-time.Hour))
		clearsOnAnyKey(h)
		if _, err := answers(e, id, "1"); err != nil {
			t.Fatal(err)
		}
		var n int
		e.DB.QueryRow("SELECT COUNT(*) FROM input_holders").Scan(&n)
		if n != 0 {
			t.Fatal("the idle hold is still there")
		}
	})
}

func TestAlwaysKeepsARuleOnlyAfterThePressLandsAndNeverForATruncatedTitle(t *testing.T) {
	var dlg capture
	for _, c := range captures(t) {
		if c.Name == "yes_no_dialog" {
			dlg = c
		}
	}
	e, h := testEnv(t)
	shown(t, e, h, dlg.Screen)
	block, _ := e.OpenBlockOf("backend")

	// the digit leaves the dialog open: the press did not land, so nothing is remembered
	if ok, said := e.Answer(block.ID, Choice{Number: 1, Always: true}); ok || len(e.Rules.List()) != 0 {
		t.Fatalf("%v %q %v", ok, said, e.Rules.List())
	}
	clearsOnAnyKey(h)
	ok, said := e.Answer(block.ID, Choice{Number: 1, Always: true})
	if !ok || !strings.HasSuffix(said, "chose 1. Yes - and will again") {
		t.Fatalf("%v %q", ok, said)
	}
	if got := e.Rules.List(); len(got) != 1 || got[0].Title != "Overwrite the file?" || got[0].Label != "Yes" {
		t.Fatalf("%v", got)
	}

	// an agent's session cannot write the store: the press still lands, the rule is not kept, and the answer says so
	e, h = testEnv(t)
	shown(t, e, h, dlg.Screen)
	block, _ = e.OpenBlockOf("backend")
	clearsOnAnyKey(h)
	t.Setenv("OCP_AGENT", "backend")
	t.Setenv("HANDLINE_AGENT", "backend")
	ok, said = e.Answer(block.ID, Choice{Number: 1, Always: true})
	if !ok || !strings.Contains(said, "but not remembered") {
		t.Fatalf("%v %q", ok, said)
	}
	t.Setenv("OCP_AGENT", "")
	t.Setenv("HANDLINE_AGENT", "")
	if len(e.Rules.List()) != 0 {
		t.Fatal("an agent wrote a rule")
	}
}

func TestATruncatedTitleKeepsNoRule(t *testing.T) {
	e, _ := testEnv(t)
	ok, why := e.remember(&dialogInfo{kind: "dialog", title: term.TruncatedTitle, vendor: "claude"}, "Yes")
	if ok || !strings.Contains(why, "no rule can be kept") || len(e.Rules.List()) != 0 {
		t.Fatalf("%v %q", ok, why)
	}
}

func TestAFolderTrustAnswerIsRememberedAsARootOnlyWhenNotAlreadyDeclared(t *testing.T) {
	e, _ := testEnv(t)
	folder := filepath.Join(t.TempDir(), "repo")
	os.MkdirAll(folder, 0o755)
	d := &dialogInfo{kind: kindTrust, folder: folder}
	if ok, _ := e.remember(d, trustLabel); !ok || !e.Trust.IsDeclared(folder) {
		t.Fatal("the folder was not declared")
	}
	if ok, why := e.remember(d, trustLabel); !ok || why != "already trusted" {
		t.Fatalf("%v %q", ok, why)
	}
}

var trustScreen = []string{"Quick safety check: Is this a project you created or one you trust?", "", " Accessing workspace:", " /work/project", "",
	"❯ 1. Yes, I trust this folder", "  2. No, exit"}

func TestTheFolderTrustScreenIsOfferedWithItsFolderAndAnsweredWithDownArrowThenOneEnter(t *testing.T) {
	e, h := testEnv(t)
	seedAgent(t, e, "backend", "%1")
	h.set("%1", trustScreen)
	d, err := e.dialogOf("backend")
	if err != nil || d == nil || d.kind != kindTrust || d.title != "Trust /work/project?" || len(d.options) != 1 || d.options[0].Label != trustLabel {
		t.Fatalf("%+v %v", d, err)
	}
	ok, said := e.pressChoice("backend", d.title, 1, false)
	if !ok || said != "backend: trusted" || len(h.sent) != 2 || h.sent[0] != `"\x1b[B"/false` || h.sent[1] != `""/true` {
		t.Fatalf("%v %q %q", ok, said, h.sent)
	}
	// a number the dialog does not offer, or another title: nothing more is sent
	h.sent = nil
	if ok, _ := e.pressChoice("backend", d.title, 2, false); ok || len(h.sent) != 0 {
		t.Fatalf("%v %q", ok, h.sent)
	}
	if ok, _ := e.pressChoice("backend", "Trust /elsewhere?", 1, false); ok || len(h.sent) != 0 {
		t.Fatalf("%v %q", ok, h.sent)
	}
}

func TestATrustScreenWithNoReadableFolderIsNotOfferedAndNothingIsSent(t *testing.T) {
	e, h := testEnv(t)
	seedAgent(t, e, "backend", "%1")
	h.set("%1", []string{trustScreen[0], "", "❯ 1. Yes, I trust this folder", "  2. No, exit", "Enter to confirm · Esc to cancel"})
	if d, err := e.dialogOf("backend"); d != nil || err != nil {
		t.Fatalf("%+v %v", d, err)
	}
	if ok, _ := e.pressChoice("backend", "Is this a project you created or one you trust?", 1, false); ok || len(h.sent) != 0 {
		t.Fatalf("%v %q", ok, h.sent)
	}
	register(e)
	// through the handler it is no prompt at all: the watch opens nothing and an answer is stale
	h.set("%1", trustScreen)
	e.WatchOnce(t.Logf)
	_, err := answers(e, "any", "1")
	refusal(t, err, "stale_prompt")
	if len(h.sent) != 0 {
		t.Fatalf("%q", h.sent)
	}
}

func TestIfTheFirstTrustKeyFailsNoEnterIsSent(t *testing.T) {
	e, h := testEnv(t)
	seedAgent(t, e, "backend", "%1")
	h.set("%1", trustScreen)
	h.failAt = 1
	ok, said := e.pressChoice("backend", "Trust /work/project?", 1, false)
	if ok || !strings.Contains(said, "could not accept the trust dialog") || len(h.sent) != 1 {
		t.Fatalf("%v %q %q", ok, said, h.sent)
	}
	h.sent, h.failAt = nil, 2
	ok, said = e.pressChoice("backend", "Trust /work/project?", 1, false)
	if ok || !strings.Contains(said, "could not confirm the trust prompt") || len(h.sent) != 2 {
		t.Fatalf("%v %q %q", ok, said, h.sent)
	}
}

type fakeStructured struct {
	err    error
	called []string
}

func (f *fakeStructured) Has(agent string) bool { return agent == "backend" }
func (f *fakeStructured) Answer(agent, prompt string, allow bool, _, by string) error {
	f.called = append(f.called, fmt.Sprint(agent, prompt, allow, by))
	return f.err
}

func TestAStructuredAgentIsAnsweredByCallbackAndNeverByKeys(t *testing.T) {
	e, h := testEnv(t)
	register(e)
	f := &fakeStructured{}
	e.Structured = f
	got, err := answers(e, "fp1", "no")
	if err != nil || got.(map[string]any)["said"] != "backend: answered No" || len(f.called) != 1 || f.called[0] != "backendfp1falsecasey" || len(h.sent) != 0 {
		t.Fatalf("%v %v %v %v", got, err, f.called, h.sent)
	}
	if _, err := answers(e, "fp1", "maybe"); err == nil {
		t.Fatal("an unknown choice was taken")
	}
	f.err = structured.ErrStale
	if _, err := answers(e, "fp1", "yes"); err == nil || strings.Contains(err.Error(), "backend: answered") {
		t.Fatalf("a stale answer must be refused: %v", err)
	}
}
