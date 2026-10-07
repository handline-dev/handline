package textsafe

import (
	"reflect"
	"testing"
)

func TestDrawableTakesOutWhatATerminalWouldObey(t *testing.T) {
	cases := map[string][]string{
		"plain":                        {"plain"},
		"a\x1b[31mred\x1b[0m b":        {"ared b"},
		"t\x1b]0;title\x07x":           {"tx"},
		"a\rb\x0bc\x0cd":               {"a", "b", "c", "d"},
		"bell\x07 and \u009b esc\x1bz": {"bell and  escz"},
		"a\tb":                         {"a   b"},
		"":                             {""},
		"x\n\ny":                       {"x", "", "y"},
		"line two":                     {"line", "two"},
	}
	for in, want := range cases {
		if got := Drawable(in, 4); !reflect.DeepEqual(got, want) {
			t.Errorf("%q: %q, want %q", in, got, want)
		}
	}
	if got := Text("a\tb\x1b[1m\nc"); got != "a\tb\nc" {
		t.Errorf("%q", got)
	}
}

func TestOneline(t *testing.T) {
	if got := Oneline("a\nb\x07c​d"); got != "a bc d" {
		t.Errorf("%q", got)
	}
}

func TestHasUnsafe(t *testing.T) {
	for s, want := range map[string]bool{"worker": false, "a b": false, "a\tb": true, "a\nb": true, "a b": true, "a\u009bb": true, "a\x7fb": true, "é": false} {
		if HasUnsafe(s) != want {
			t.Errorf("%q", s)
		}
	}
}
