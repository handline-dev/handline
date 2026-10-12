package sessionrpc

import (
	"strings"
	"testing"

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
	r.e.Structured = func(a string) bool { return r.pr.live[a] }
	r.pr.live["ann"] = false // a terminal agent
	h := &host{rows: []string{"> "}}
	r.e.Holds = &inputhold.Store{DB: r.e.DB, Now: r.e.Now}
	r.e.Term = term.New(h, nil)
	return r, h, r.dial(false)
}

func TestTakeAndReleaseAreTheHoldersAloneAndAStructuredAgentHasNoInputToHold(t *testing.T) {
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
	r.pr.live["ann"] = true
	if g := refused(c.call("input.take", map[string]any{"agent": "ann", "request_id": rid("t3")})); g != "unsupported" {
		t.Fatalf("a structured agent has no terminal: gate %q", g)
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
	h.rows = []string{"ron@mac ~ $"} // no composer box: not a session screen, so a line would answer nothing it can see
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
