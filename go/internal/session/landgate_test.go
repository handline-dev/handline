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
		{"hl land a b --tree t --fix /tmp/fix --squash", "land", "a"},
		{"hl land --squash a b --tree t", "land", "a"},
		{"hl land --pass a --fix /tmp/fix", "", ""},
		{"hl land --pass a --squash", "", ""},
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

// The same table runs against landgate.misses in tests/infra/test_land.py.
func TestMisses(t *testing.T) {
	keyed := func(string) string { return "k" }
	for _, c := range []struct {
		cmd    string
		misses bool
	}{
		{"hl land --pass abc", false},
		{"hl land abc 'm' --tree def", false},
		{"hl land --pass abc 2>&1", true},
		{"cd /r && hl land abc 'm' --tree def", true},
		{"hl land abc 'costs $5' --tree def", true},
		{"hl land --pass abc | tail -3", true},
		{"hl land --check abc 2>&1", false},
		{"hl land --list | head", false},
		{"git log | grep land", false},
		{"echo hl landed --pass", false},
		{`hl task handoff T1 worker "PASS recorded; land it: hl land --pass abc1234"`, false},
		{"hl say manager 'please run hl land --pass abc1234'", false},
		{"hl say b - <<'EOF'\nhl land --pass abc\nEOF", false},
		{"echo x; hl land --pass abc", true},
	} {
		got := Misses(keyed, "a", "Bash", map[string]any{"command": c.cmd})
		if (got != "") != c.misses {
			t.Errorf("%q: misses %v, want %v", c.cmd, got != "", c.misses)
		}
	}
	if Misses(func(string) string { return "" }, "a", "Bash", map[string]any{"command": "hl land --pass abc 2>&1"}) != "" {
		t.Error("a session with no key is not told to retype: retyping would not help it")
	}
	if Misses(keyed, "a", "Read", map[string]any{"command": "hl land --pass abc 2>&1"}) != "" {
		t.Error("only a Bash call is read")
	}
}
