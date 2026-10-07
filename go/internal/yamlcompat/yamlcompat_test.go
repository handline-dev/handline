package yamlcompat

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"
)

// cases are sources with what the bundled Python parser (handline/infra/yamlcompat.py) read them as, or refused.
var cases = []struct {
	src, want string // want is JSON; empty when the source is refused
	refused   bool
}{
	{"a: 1\nb: two\nc: true\nd: no\ne: null\nf: ~\n", "{\"a\": 1, \"b\": \"two\", \"c\": true, \"d\": false, \"e\": null, \"f\": null}", false},
	{"a:\n  b: 1\n  c:\n    - x\n    - y\n", "{\"a\": {\"b\": 1, \"c\": [\"x\", \"y\"]}}", false},
	{"a:\n- x\n- y\nb: 2\n", "{\"a\": [\"x\", \"y\"], \"b\": 2}", false},
	{"items:\n  - name: a\n    n: 1\n  - name: b\n    n: 2\n", "{\"items\": [{\"name\": \"a\", \"n\": 1}, {\"name\": \"b\", \"n\": 2}]}", false},
	{"k: [a, 'b c', 3, 1.5]\nm: {x: 1, y: \"z\"}\n", "{\"k\": [\"a\", \"b c\", 3, 1.5], \"m\": {\"x\": 1, \"y\": \"z\"}}", false},
	{"a: 0x1F\nb: -0b11\nc: 010\nd: 1.0e+3\ne: 1e3\nf: .5\ng: -.5\n", "{\"a\": 31, \"b\": -3, \"c\": 8, \"d\": 1000.0, \"e\": \"1e3\", \"f\": 0.5, \"g\": \"-.5\"}", false},
	{"url: http://example.com/a#frag\nr: go#1  # comment\n", "{\"url\": \"http://example.com/a#frag\", \"r\": \"go#1\"}", false},
	{"# only a comment\n", "{}", false},
	{"", "{}", false},
	{"---\na: 1\n", "{\"a\": 1}", false},
	{"a: 1\n---\nb: 2\n", "", true},
	{"a: &x 1\n", "", true},
	{"a: |\n  text\n", "", true},
	{"a: *x\n", "", true},
	{"a:\n\tb: 1\n", "", true},
	{"a: 12:30\n", "", true},
	{"a: 2026-01-01\n", "", true},
	{"a: .inf\n", "", true},
	{"a: 1_000\n", "", true},
	{"a: \"x\\ny\"\n", "", true},
	{"a: \"q\\\"r\\\\s\"\n", "{\"a\": \"q\\\"r\\\\s\"}", false},
	{"a: 'it''s'\n", "{\"a\": \"it's\"}", false},
	{"1: a\n", "", true},
	{"on: a\n", "", true},
	{"'on': a\n", "{\"on\": \"a\"}", false},
	{"a: [1, 2\n", "", true},
	{"a: {x 1}\n", "", true},
	{"a: b]\n", "", true},
	{"- a\n- b: 1\n  c: 2\n- [1, 2]\n- {k: v}\n-\n  - z\n", "[\"a\", {\"b\": 1, \"c\": 2}, [1, 2], {\"k\": \"v\"}, [\"z\"]]", false},
	{"a: yes\nb: Yes\nc: y\nd: yEs\n", "{\"a\": true, \"b\": true, \"c\": \"y\", \"d\": \"yEs\"}", false},
	{"a:b: c\n", "{\"a:b\": \"c\"}", false},
	{"- a:b\n- http://x\n", "[\"a:b\", \"http://x\"]", false},
	{"a: 'x\n", "", true},
	{"a: \"x\" y\n", "", true},
	{"a: b\n c: d\n", "", true},
	{"  a: 1\n  b: 2\n", "{\"a\": 1, \"b\": 2}", false},
	{"a: 1\n b: 2\n", "", true},
	{"k: x > y\n", "", true},
	{"k:\n  - a\n  -\n  - c\n", "{\"k\": [\"a\", [\"c\"]]}", false},
}

func TestParseReadsWhatTheBundledParserReads(t *testing.T) {
	for _, c := range cases {
		got, err := Parse(c.src)
		if c.refused {
			var ce *ConfigError
			if err == nil || !asConfigError(err, &ce) {
				t.Errorf("%q: want a ConfigError, got %v, %v", c.src, got, err)
			}
			continue
		}
		if err != nil {
			t.Errorf("%q: %v", c.src, err)
			continue
		}
		b, _ := json.Marshal(got)
		var gv, wv any
		json.Unmarshal(b, &gv)
		json.Unmarshal([]byte(c.want), &wv)
		if !reflect.DeepEqual(gv, wv) {
			t.Errorf("%q: got %s, want %s", c.src, b, c.want)
		}
	}
}

func asConfigError(err error, target **ConfigError) bool {
	ce, ok := err.(*ConfigError)
	*target = ce
	return ok
}

func TestIntegersAndFloatsAreKeptApart(t *testing.T) {
	got, err := Parse("a: 1\nb: 1.0\nc: 0x10\nd: -7\n")
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]any{"a": int64(1), "b": 1.0, "c": int64(16), "d": int64(-7)}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("%#v", got)
	}
	if _, err := Parse("a: 99999999999999999999\n"); err == nil {
		t.Fatal("a number past int64 was read")
	}
}

func TestDumpWritesWhatParseReadsBack(t *testing.T) {
	pairs := []Pair{{"name", "worker"}, {"n", 3}, {"flag", true}, {"f", 1e16}, {"none", nil}, {"word", "yes"}, {"colon", "a: b"}, {"num", "12"}, {"quote", `say "hi"`}, {"empty", ""}}
	text, err := Dump(pairs)
	if err != nil {
		t.Fatal(err)
	}
	back, err := Parse(text)
	if err != nil {
		t.Fatalf("%v\n%s", err, text)
	}
	want := map[string]any{"name": "worker", "n": int64(3), "flag": true, "f": 1e16, "none": nil, "word": "yes", "colon": "a: b", "num": "12", "quote": `say "hi"`, "empty": ""}
	if !reflect.DeepEqual(back, want) {
		t.Fatalf("%#v\n%s", back, text)
	}
	if !strings.Contains(text, "f: 1.0e+16\n") {
		t.Fatalf("an exponent needs a decimal point: %s", text)
	}
	for _, bad := range []Pair{{"k", []any{1}}, {"k", "a\x1bb"}, {"k", "a\nb"}} {
		if _, err := Dump([]Pair{bad}); err == nil {
			t.Errorf("Dump wrote %#v", bad)
		}
	}
}

func TestIsPlainText(t *testing.T) {
	for name, want := range map[string]bool{"worker": true, "a.b-c": true, "on": false, "12": false, "a: b": false, "x,y": false} {
		if IsPlainText(name) != want {
			t.Errorf("%q: want %v", name, want)
		}
	}
}

func TestRefuseUnsafeValues(t *testing.T) {
	if err := RefuseUnsafeValues(map[string]any{"a": "ok", "b": 1}, "agent.yaml"); err != nil {
		t.Fatal(err)
	}
	if err := RefuseUnsafeValues(map[string]any{"a": "x\u009by"}, "agent.yaml"); err == nil {
		t.Fatal("a C1 control was read")
	}
}
