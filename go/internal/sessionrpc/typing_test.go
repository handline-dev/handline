package sessionrpc

import (
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/inputhold"
	"github.com/handline/handline/go/internal/lock"
	"github.com/handline/handline/go/internal/term"
)

// host is a terminal that keeps what is sent to it and shows the rows a test sets.
type host struct {
	rows []string
	sent []string
}

func (h *host) Name() string                                   { return "fake" }
func (h *host) Available() bool                                { return true }
func (h *host) ListTerminals() map[string]bool                 { return map[string]bool{"%1": true} }
func (h *host) WaitIdle(string, int, func([]string) bool) bool { return true }
func (h *host) ReadScreen(string, int) *term.Screen {
	return &term.Screen{Tail: h.rows, Source: "screen"}
}
func (h *host) Send(_, text string, enter bool) (bool, string) {
	if enter {
		text += "<enter>"
	}
	h.sent = append(h.sent, text)
	return true, ""
}

func typingRig(t *testing.T) (*rig, *host, *conn) {
	r := newRig(t)
	h := &host{rows: []string{"> "}}
	r.e.Holds = &inputhold.Store{DB: r.e.DB, Now: r.e.Now}
	r.e.Term = term.New(h, nil)
	return r, h, r.dial(false)
}

func TestTakeAndReleaseAreTheHoldersAlone(t *testing.T) {
	r, _, c := typingRig(t)
	got := result(t, c.call("input.take", map[string]any{"agent": "ann", "request_id": rid("t1")}))
	if got["holder"].(map[string]any)["name"] != c.name {
		t.Fatalf("take %v", got)
	}
	if row, _ := r.e.Holds.Holder("ann"); row == nil || row.Name != c.name {
		t.Fatalf("holder %v", row)
	}
	// someone else's hold is not the caller's to release or take
	r.e.DB.Exec("UPDATE input_holders SET name = 'sam'")
	if g := refused(c.call("input.take", map[string]any{"agent": "ann", "request_id": rid("t2")})); g != "input_holder" {
		t.Fatalf("gate %q", g)
	}
	if g := refused(c.call("input.release", map[string]any{"agent": "ann", "request_id": rid("r1")})); g != "input_holder" {
		t.Fatalf("gate %q", g)
	}
	r.e.DB.Exec("UPDATE input_holders SET name = ?", c.name)
	result(t, c.call("input.release", map[string]any{"agent": "ann", "request_id": rid("r2")}))
	if row, _ := r.e.Holds.Holder("ann"); row != nil {
		t.Fatalf("released, got %v", row)
	}
	if said := result(t, c.call("input.release", map[string]any{"agent": "ann", "request_id": rid("r3")}))["said"]; !strings.Contains(said.(string), "already") {
		t.Fatalf("said %v", said)
	}
}

func TestForceTakesFromAnotherHolderWhoIsTold(t *testing.T) {
	r, _, c := typingRig(t)
	told := []string{}
	r.e.Holds.Changed = func(_ string, h []map[string]any) { told = append(told, h[0]["name"].(string)) }
	r.e.Holds.Take("ann", "device", "sam")
	if g := refused(c.call("input.take", map[string]any{"agent": "ann", "request_id": rid("f1")})); g != "input_holder" {
		t.Fatalf("without force: gate %q", g)
	}
	if f := badField(c.call("input.take", map[string]any{"agent": "ann", "force": "yes", "request_id": rid("f2")})); f != "force" {
		t.Fatalf("field %q", f)
	}
	got := result(t, c.call("input.take", map[string]any{"agent": "ann", "force": true, "request_id": rid("f3")}))
	if got["holder"].(map[string]any)["name"] != c.name {
		t.Fatalf("take %v", got)
	}
	if strings.Join(told, ",") != "sam,"+c.name { // the holder notice reaches every pane on the terminal, sam's too
		t.Fatalf("told %v", told)
	}
}

func TestForceDoesNotTypeIntoAnAgentsTurn(t *testing.T) {
	r, h, c := typingRig(t)
	busy := lock.TakeTerminal(r.e.Home, "ann") // the tick, mid-turn
	defer busy.Release()
	result(t, c.call("input.take", map[string]any{"agent": "ann", "force": true, "request_id": rid("f1")}))
	if g := refused(c.call("session.type", map[string]any{"agent": "ann", "text": "x", "request_id": rid("f2")})); g != "terminal_lock" {
		t.Fatalf("gate %q", g)
	}
	if len(h.sent) != 0 {
		t.Fatalf("sent %q", h.sent)
	}
}

func TestKeysAndTypeSendWhatTheyNameAndSkipTheDialogCheck(t *testing.T) {
	r, h, c := typingRig(t)
	h.rows = []string{"Do you want to proceed?", "Enter to confirm · Esc to cancel"} // a dialog is open
	result(t, c.call("session.keys", map[string]any{"agent": "ann", "keys": []any{"down", "enter", "esc", "2"}, "request_id": rid("k1")}))
	if got := strings.Join(h.sent, "|"); got != "\x1b[B|<enter>|\x1b|2" {
		t.Fatalf("sent %q", got)
	}
	result(t, c.call("session.type", map[string]any{"agent": "ann", "text": "ls\tx", "request_id": rid("y1")}))
	if h.sent[len(h.sent)-1] != "ls\tx" {
		t.Fatalf("sent %q", h.sent)
	}
	var typed int64
	r.e.DB.QueryRow("SELECT row_id FROM cursors WHERE name = 'typed_at:ann'").Scan(&typed)
	if typed != 1_000_000 {
		t.Fatalf("typed_at %d", typed)
	}
	for _, p := range []map[string]any{
		{"agent": "ann", "keys": []any{"f13"}},
		{"agent": "ann", "keys": []any{}},
	} {
		p["request_id"] = rid("bad" + strings.Join(strings.Fields("k"), ""))
		if f := badField(c.call("session.keys", p)); f != "keys" {
			t.Fatalf("%v: field %q", p, f)
		}
	}
	if f := badField(c.call("session.type", map[string]any{"agent": "ann", "text": "a\x1b[A", "request_id": rid("y2")})); f != "text" {
		t.Fatalf("Esc is a key, not text: field %q", f)
	}
}

func TestSessionInputTypesAtAComposerAndIsRefusedAtAShellADialogTheWallAndByAnotherHolder(t *testing.T) {
	r, h, c := typingRig(t)
	call := func(n string) map[string]any {
		return c.call("session.input", map[string]any{"agent": "ann", "text": "hello", "request_id": rid(n)})
	}
	h.rows = []string{"user@host ~ $"} // no composer box: not a session screen, so a line would answer nothing it can see
	if g := refused(call("i1")); g != "screen_blocker" {
		t.Fatalf("a shell: gate %q", g)
	}
	h.rows = []string{"Do you want to proceed?", "Enter to confirm · Esc to cancel"}
	if g := refused(call("i2")); g != "screen_blocker" {
		t.Fatalf("a dialog: gate %q", g)
	}
	r.e.Wall = func(string) string { return "ann is at its usage limit" }
	if g := refused(call("i3")); g != "wall" {
		t.Fatalf("the wall: gate %q", g)
	}
	r.e.Wall = nil
	h.rows = []string{"", "────────────────────────────────", "❯ ", "────────────────────────────────", "  ? for shortcuts"}
	if got := call("i5"); got["error"] != nil || strings.Join(h.sent, "|") != "hello<enter>" {
		t.Fatalf("a session at its composer is typed at: %v %q", got, h.sent)
	}
	h.sent = nil
	r.e.Holds.Take("ann", "person", "sam")
	if g := refused(call("i4")); g != "input_holder" {
		t.Fatalf("another holds it: gate %q", g)
	}
	if len(h.sent) != 0 {
		t.Fatalf("nothing was typed: %q", h.sent)
	}
}

// ended is hooks that saw the session end: they know nothing else.
type ended struct{}

func (ended) WaitIdle(string, time.Duration) (bool, bool) { return false, false }
func (ended) Background(string) (string, bool)            { return "", false }
func (ended) Settled(string) bool                         { return false }
func (ended) Session(string) (bool, bool)                 { return false, true }

func TestSessionInputTypesNothingWhereTheHooksSawTheSessionEnd(t *testing.T) {
	rg, h, c := typingRig(t)
	rg.e.Term.Turns = ended{}
	h.rows = []string{"", "────────────────────────────────", "❯ ", "────────────────────────────────", "  ? for shortcuts"}
	r := c.call("session.input", map[string]any{"agent": "ann", "text": "hello", "request_id": rid("e1")})
	if g := refused(r); g != "state" || len(h.sent) != 0 {
		t.Fatalf("a composer still drawn: gate %q, sent %q", g, h.sent)
	}
}

func TestATypingCallWhileTheTickHoldsTheTerminalLockIsRefusedAtOnce(t *testing.T) {
	r, h, c := typingRig(t)
	busy := lock.TakeTerminal(r.e.Home, "ann")
	defer busy.Release()
	if g := refused(c.call("session.type", map[string]any{"agent": "ann", "text": "x", "request_id": rid("l1")})); g != "terminal_lock" {
		t.Fatalf("gate %q", g)
	}
	if len(h.sent) != 0 {
		t.Fatalf("sent %q", h.sent)
	}
}

func TestInterruptPressesEscOnATerminalAgentsPaneBehindTheTypingGates(t *testing.T) {
	r, h, c := typingRig(t)
	r.e.InterruptTerminal = r.e.PressEsc
	c = r.dial(false)                                                                        // the method is registered on a dial that has the port
	result(t, c.call("input.take", map[string]any{"agent": "ann", "request_id": rid("t1")})) // the caller's own hold lets it through
	got := result(t, c.call("session.interrupt", map[string]any{"agent": "ann", "request_id": rid("i1")}))
	if got["said"] != "ann: interrupted" || strings.Join(h.sent, "|") != term.Escape {
		t.Fatalf("%v sent %q", got, h.sent)
	}
	r.e.DB.Exec("UPDATE input_holders SET name = 'sam'")
	if g := refused(c.call("session.interrupt", map[string]any{"agent": "ann", "request_id": rid("i2")})); g != "input_holder" {
		t.Fatalf("someone else holds its input: %q", g)
	}
	r.e.DB.Exec("DELETE FROM input_holders")
	hold := lock.TakeTerminal(r.e.Home, "ann") // someone is typing at it this moment
	if g := refused(c.call("session.interrupt", map[string]any{"agent": "ann", "request_id": rid("i3")})); g != "terminal_lock" {
		t.Fatalf("an Esc into the middle of something being typed: %q", g)
	}
	hold.Release()
	r.e.DB.Exec("UPDATE agent_state SET terminal = '' WHERE agent = 'ann'")
	if g := refused(c.call("session.interrupt", map[string]any{"agent": "ann", "request_id": rid("i4")})); g != "state" {
		t.Fatalf("no terminal: %q", g)
	}
}

// shellHost is the host with one plain shell, %7.
type shellHost struct{ *host }

func (shellHost) CreateShell(string, string) (string, string) { return "", "not here" }
func (shellHost) Shells() ([]term.Shell, bool) {
	return []term.Shell{{Handle: "%7", Name: "logs", Cwd: "/tmp", Command: "zsh"}}, true
}

// A plain shell's input is taken and given back by its terminal handle, and terminal.close closes it unless someone else holds it.
func TestAShellIsTakenAndClosedByItsTerminal(t *testing.T) {
	r, h, _ := typingRig(t)
	r.e.Term = term.New(shellHost{h}, nil)
	c := r.dial(false)
	if m := c.call("input.take", map[string]any{"terminal": "%1", "request_id": rid("n1")}); m["error"] == nil {
		t.Fatal("a pane that is not a shell was taken by its handle")
	}
	result(t, c.call("input.take", map[string]any{"terminal": "%7", "request_id": rid("t1")}))
	if row, _ := r.e.Holds.Holder(term.ShellKey("%7")); row == nil || row.Name != c.name {
		t.Fatalf("holder %v", row)
	}
	r.e.DB.Exec("UPDATE input_holders SET name = 'sam'")
	if g := refused(c.call("terminal.close", map[string]any{"terminal": "%7", "request_id": rid("c1")})); g != "input_holder" || len(r.closed) != 0 {
		t.Fatalf("closed under another's hold: %q %v", g, r.closed)
	}
	r.e.DB.Exec("UPDATE input_holders SET name = ?", c.name)
	result(t, c.call("terminal.close", map[string]any{"terminal": "%7", "request_id": rid("c2")}))
	if len(r.closed) != 1 || r.closed[0] != "%7" {
		t.Fatalf("closed %v", r.closed)
	}
	if row, _ := r.e.Holds.Holder(term.ShellKey("%7")); row != nil {
		t.Fatal("the hold outlived its shell")
	}
}
