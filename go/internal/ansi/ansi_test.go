package ansi

import (
	"encoding/json"
	"reflect"
	"testing"
)

// Expected values are what handline/infra/ansi.py gives for the same rows.
func TestParse(t *testing.T) {
	cases := []struct{ in, text, spans string }{
		{"plain  ", "plain", `[]`},
		{"\x1b[1;31mred\x1b[0m ok", "red ok", `[{"from":0,"to":3,"style":{"bold":true,"fg":1}}]`},
		{"\x1b[38;5;200mx\x1b[48;2;1;2;3m  \x1b[0m", "x", `[{"from":0,"to":1,"style":{"fg":200}},{"from":1,"to":3,"style":{"bg":"#010203","fg":200}}]`},
		{"\x1b[7m   \x1b[0m", "", `[{"from":0,"to":3,"style":{"inverse":true}}]`},
		{"a\x1b]0;t\x07b\x1b[2Jc", "abc", `[]`},
		{"\x1b[1mb\x1b[22mx\x1b[38:2::9:8:7my", "bxy", `[{"from":0,"to":1,"style":{"bold":true}},{"from":2,"to":3,"style":{"fg":"#090807"}}]`},
	}
	for _, c := range cases {
		text, spans := Parse(c.in)
		out := []any{}
		for _, s := range spans {
			out = append(out, s.Map())
		}
		b, _ := json.Marshal(out)
		var got, want any
		json.Unmarshal(b, &got)
		json.Unmarshal([]byte(c.spans), &want)
		if text != c.text || !reflect.DeepEqual(got, want) {
			t.Errorf("Parse(%q) = %q %s, want %q %s", c.in, text, b, c.text, c.spans)
		}
	}
}
