package prompts

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/term"
)

// host is a terminal host with one canned screen per handle; a key sent to it can change the screen.
type host struct {
	mu      sync.Mutex
	screens map[string][]string
	sent    []string
	onSend  func(h *host, handle, text string, enter bool)
}

func (h *host) Name() string                                   { return "fake" }
func (h *host) Available() bool                                { return true }
func (h *host) ListTerminals() map[string]bool                 { return map[string]bool{} }
func (h *host) WaitIdle(string, int, func([]string) bool) bool { return true }
func (h *host) ReadScreen(handle string, limit int) *term.Screen {
	h.mu.Lock()
	defer h.mu.Unlock()
	rows, ok := h.screens[handle]
	if !ok {
		return nil
	}
	if len(rows) > limit {
		rows = rows[len(rows)-limit:]
	}
	return &term.Screen{Tail: append([]string{}, rows...), Source: "screen"}
}
func (h *host) Send(handle, text string, enter bool) (bool, string) {
	h.mu.Lock()
	h.sent = append(h.sent, fmt.Sprintf("%q/%v", text, enter))
	fn := h.onSend
	h.mu.Unlock()
	if fn != nil {
		fn(h, handle, text, enter)
	}
	return true, ""
}
func (h *host) set(handle string, rows []string) {
	h.mu.Lock()
	h.screens[handle] = rows
	h.mu.Unlock()
}

func testEnv(t *testing.T) (*Env, *host) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("HANDLINE_HOME", home)
	t.Setenv("HANDLINE_TEST_ROOT", t.TempDir()) // the protected store lives under the user home
	t.Setenv("OCP_AGENT", "")
	t.Setenv("HANDLINE_AGENT", "")
	db, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })
	e := New(db, home, &reads.Env{DB: db, Home: home, Started: time.Now()})
	h := &host{screens: map[string][]string{}}
	e.Term = term.New(h, e.driverFor)
	e.Term.Settle, e.Term.Gap = 0, 0
	return e, h
}

func seedAgent(t *testing.T, e *Env, agent, handle string) {
	t.Helper()
	if _, err := e.DB.Exec("INSERT INTO agent_state(agent, state, updated, terminal) VALUES(?, 'IDLE', 0, ?)", agent, handle); err != nil {
		t.Fatal(err)
	}
}

func noteWait(t *testing.T, e *Env, agent, command string, after int) {
	t.Helper()
	dir := filepath.Join(e.Home, "waiting")
	os.MkdirAll(dir, 0o755)
	b, _ := json.Marshal(map[string]any{"tool": "Bash", "command": command, "reason": "", "since": time.Now().Unix(), "after_event_id": after})
	if err := os.WriteFile(filepath.Join(dir, agent+".json"), b, 0o644); err != nil {
		t.Fatal(err)
	}
}

type capture struct {
	Name   string
	Screen []string
	Expect struct {
		Kind    string
		Tool    any
		Command any
		Reason  any
		Options []string
	}
}

func captures(t *testing.T) []capture {
	t.Helper()
	files, _ := filepath.Glob("../../../contract/prompts/*.json")
	if len(files) < 4 {
		t.Fatalf("captures: %v", files)
	}
	var out []capture
	for _, f := range files {
		b, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		var c capture
		if err := json.Unmarshal(b, &c); err != nil {
			t.Fatal(err)
		}
		out = append(out, c)
	}
	return out
}

func openPrompt(t *testing.T, e *Env, agent string) map[string]any {
	t.Helper()
	p, err := e.Reads.OpenPrompt(agent)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := json.Marshal(p)
	var m map[string]any
	json.Unmarshal(b, &m)
	return m
}

func TestEveryCaptureReadsAsTheFixtureSays(t *testing.T) {
	for _, c := range captures(t) {
		t.Run(c.Name, func(t *testing.T) {
			e, h := testEnv(t)
			seedAgent(t, e, "a.one", "%1")
			h.set("%1", c.Screen)
			if _, err := e.WatchOnce(t.Logf); err != nil {
				t.Fatal(err)
			}
			got := openPrompt(t, e, "a.one")
			if got == nil {
				t.Fatalf("no prompt listed")
			}
			var keys []string
			for _, o := range got["options"].([]any) {
				keys = append(keys, o.(map[string]any)["key"].(string))
			}
			if got["kind"] != c.Expect.Kind || got["tool"] != c.Expect.Tool || got["command"] != c.Expect.Command || got["reason"] != c.Expect.Reason ||
				strings.Join(keys, ",") != strings.Join(c.Expect.Options, ",") {
				t.Fatalf("got %v, want %+v", got, c.Expect)
			}
			if len(got["lines"].([]any)) == 0 {
				t.Fatal("the raw lines are always there")
			}
		})
	}
}

func TestAScreenThatCannotBeReadChangesNothing(t *testing.T) {
	e, h := testEnv(t)
	seedAgent(t, e, "a.one", "%1")
	cs := captures(t)
	h.set("%1", cs[0].Screen)
	e.WatchOnce(t.Logf)
	before := openPrompt(t, e, "a.one")
	delete(h.screens, "%1")
	e.WatchOnce(t.Logf)
	if after := openPrompt(t, e, "a.one"); fmt.Sprint(after) != fmt.Sprint(before) {
		t.Fatalf("an unreadable screen moved the row: %v -> %v", before, after)
	}
	h.set("%1", []string{"just output"})
	e.WatchOnce(t.Logf)
	if openPrompt(t, e, "a.one") != nil {
		t.Fatal("a screen with no prompt closes the row")
	}
}

func boxless(t *testing.T) capture {
	for _, c := range captures(t) {
		if c.Name == "permission_bash_boxless" {
			return c
		}
	}
	t.Fatal("no boxless capture")
	return capture{}
}

func TestAPermissionPromptWithAHookRecordIsAnsweredWithTheNumberOfItsYes(t *testing.T) {
	e, h := testEnv(t)
	c := boxless(t)
	seedAgent(t, e, "backend", "%1")
	h.set("%1", c.Screen)
	noteWait(t, e, "backend", "rm -rf build", 7)
	e.WatchOnce(t.Logf)
	block, _ := e.OpenBlockOf("backend")
	if block == nil || !block.Answerable || block.WaitID.Int64 != 7 {
		t.Fatalf("%+v", block)
	}
	h.onSend = func(h *host, handle, text string, enter bool) { h.set(handle, []string{"back at the prompt"}) }
	ok, said := e.Answer(block.ID, Choice{Yes: true})
	if !ok || said != "backend: answered Yes" || len(h.sent) != 1 || h.sent[0] != `"1"/false` {
		t.Fatalf("%v %q %v", ok, said, h.sent)
	}
	if again, _ := e.Answer(block.ID, Choice{Yes: true}); again {
		t.Fatal("an answered prompt is not answered twice")
	}
}

func TestAPromptThatChangedUnderTheAnswerIsRefusedAndNothingIsSent(t *testing.T) {
	e, h := testEnv(t)
	c := boxless(t)
	seedAgent(t, e, "backend", "%1")
	h.set("%1", c.Screen)
	noteWait(t, e, "backend", "rm -rf build", 7)
	e.WatchOnce(t.Logf)
	block, _ := e.OpenBlockOf("backend")
	changed := make([]string, len(c.Screen))
	for i, ln := range c.Screen {
		changed[i] = strings.ReplaceAll(ln, "rm -rf build", "rm -rf /")
	}
	h.set("%1", changed)
	ok, said := e.Answer(block.ID, Choice{Yes: true})
	if ok || !strings.Contains(said, "not the one shown") || len(h.sent) != 0 {
		t.Fatalf("%v %q %v", ok, said, h.sent)
	}
	// the hook moved on to another call of the same words: the screen is the same, the wait is not
	h.set("%1", c.Screen)
	noteWait(t, e, "backend", "rm -rf build", 8)
	e.Answer(block.ID, Choice{Yes: true})
	if len(h.sent) != 0 {
		t.Fatalf("an answer to a prompt whose wait id changed sent %v", h.sent)
	}
}

func TestAPermissionPromptNoHookRecordPinsIsNotAnswerable(t *testing.T) {
	e, h := testEnv(t)
	seedAgent(t, e, "backend", "%1")
	h.set("%1", boxless(t).Screen)
	e.WatchOnce(t.Logf)
	block, _ := e.OpenBlockOf("backend")
	if ok, said := e.Answer(block.ID, Choice{Yes: true}); ok || !strings.Contains(said, "no hook record") || len(h.sent) != 0 {
		t.Fatalf("%v %q %v", ok, said, h.sent)
	}
}

func TestADialogIsAnsweredByNumberAndEscDeclinesOnlyWhereTheScreenSaysSo(t *testing.T) {
	e, h := testEnv(t)
	var dlg capture
	for _, c := range captures(t) {
		if c.Name == "yes_no_dialog" {
			dlg = c
		}
	}
	seedAgent(t, e, "a.one", "%1")
	h.set("%1", dlg.Screen)
	e.WatchOnce(t.Logf)
	block, _ := e.OpenBlockOf("a.one")
	// Esc: the footer says Enter confirms and Esc cancels, and "No" only declines
	h.onSend = func(h *host, handle, text string, enter bool) { h.set(handle, []string{"gone"}) }
	if ok, said := e.Answer(block.ID, Choice{Cancel: true}); !ok || said != "a.one: cancelled (Esc)" || h.sent[0] != `"\x1b"/false` {
		t.Fatalf("%v %q %v", ok, said, h.sent)
	}
	h.set("%1", dlg.Screen)
	e.WatchOnce(t.Logf)
	block, _ = e.OpenBlockOf("a.one")
	h.sent = nil
	if ok, said := e.Answer(block.ID, Choice{Number: 2}); !ok || !strings.Contains(said, "chose 2. No") || h.sent[0] != `"2"/false` {
		t.Fatalf("%v %q %v", ok, said, h.sent)
	}
}

func TestADialogThatDoesNotGoIsStillOpenAndTheKeyIsNotRepeated(t *testing.T) {
	e, h := testEnv(t)
	for _, c := range captures(t) {
		if c.Name == "yes_no_dialog" {
			seedAgent(t, e, "a.one", "%1")
			h.set("%1", c.Screen)
		}
	}
	e.WatchOnce(t.Logf)
	block, _ := e.OpenBlockOf("a.one")
	ok, said := e.Answer(block.ID, Choice{Number: 1})
	// the digit left the same dialog open, the highlighted option is not 1's: nothing more is sent but one digit
	if ok || !strings.Contains(said, "still on screen") || len(h.sent) != 1 {
		t.Fatalf("%v %q %v", ok, said, h.sent)
	}
}
