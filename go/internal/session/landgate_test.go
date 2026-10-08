package session

import (
	"testing"
	"time"
)

// The expected pairs were read off the Python landgate.parse for the same commands.
func TestParseLand(t *testing.T) {
	cases := []struct{ cmd, verb, sha string }{
		{"hl land --pass abc123", "land_pass", "abc123"},
		{"hl land abc 'msg here' --tree def", "land", "abc"},
		{"/usr/local/bin/hl land --pass abc", "land_pass", "abc"},
		{"hl land --check", "", ""},
		{"hl land --list", "", ""},
		{"hl land --pass abc; rm x", "", ""},
		{"hl land --pass abc && true", "", ""},
		{"hl land --pass $(x)", "", ""},
		{"hl land --pass abc # c", "", ""},
		{"hl land --pass 'a b'", "land_pass", "a b"},
		{"hl land --pass", "", ""},
		{"hl land --pass a b", "", ""},
		{"hl land a --tree t", "", ""},
		{"hl land a b --tree t", "land", "a"},
		{"ocp land --pass a", "land_pass", "a"},
		{"git land --pass a", "", ""},
		{`hl land --pass "a'b"`, "land_pass", "a'b"},
		{`hl land --pass a\ b`, "land_pass", "a b"},
		{"hl land --pass ''", "", ""},
		{"hl land --pass 'abc", "", ""},
		{`hl land --pass abc\`, "", ""},
		{`hl land a "m\"x\q" --tree t`, "land", "a"},
		{"hl land --pass a>f", "", ""},
		{"hl land --pass a >f", "", ""},
		{"hl land a --tree", "", ""},
		{"hl land --tree t a b", "land", "a"},
		{"hl  land   --pass   x", "land_pass", "x"},
		// --pass is read off the whole list, even where --tree's value goes.
		{"hl land abc msg --tree --pass", "", ""},
		{"hl land --tree --pass abc", "land_pass", "abc"},
	}
	for _, c := range cases {
		verb, sha, ok := ParseLand("Bash", map[string]any{"command": c.cmd})
		if verb != c.verb || sha != c.sha || ok != (c.verb != "") {
			t.Errorf("%q -> %q %q %v, want %q %q", c.cmd, verb, sha, ok, c.verb, c.sha)
		}
	}
	if _, _, ok := ParseLand("Write", map[string]any{"command": "hl land --pass a"}); ok {
		t.Error("a tool other than Bash vouched")
	}
	if _, _, ok := ParseLand("Bash", "hl land --pass a"); ok {
		t.Error("a non-map input vouched")
	}
}

func TestAuthorize(t *testing.T) {
	var sent map[string]any
	v := LandVoucher{
		KeyHeld: func(a string) string {
			if a == "worker" {
				return "k1"
			}
			return ""
		},
		Send: func(req map[string]any, timeout time.Duration) map[string]any {
			sent = req
			return map[string]any{"ok": true}
		},
	}
	land := map[string]any{"command": "hl land --pass abc"}
	if !v.Authorize("worker", "Bash", land) {
		t.Fatal("not vouched")
	}
	if sent["verb"] != "land_authorize" || sent["land_verb"] != "land_pass" || sent["sha"] != "abc" || sent["key"] != "k1" || sent["agent"] != "worker" {
		t.Errorf("sent %v", sent)
	}
	sent = nil
	if v.Authorize("other", "Bash", land) || sent != nil {
		t.Error("vouched without a key")
	}
	if v.Authorize("worker", "Bash", map[string]any{"command": "hl land --check"}) || sent != nil {
		t.Error("vouched for a command that sends no verb")
	}
	v.Send = func(map[string]any, time.Duration) map[string]any { return nil }
	if v.Authorize("worker", "Bash", land) {
		t.Error("vouched on no reply")
	}
}
