package requestref

import (
	"encoding/json"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
)

func TestResolveTakesAnIdOrARefOnlyForAViewerWhoMayReadIt(t *testing.T) {
	home := t.TempDir()
	q, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer q.Close()
	// all three begin 3f9a0c; alice may not read 3, bob to carol
	if _, err := q.Exec("INSERT INTO requests(id, requester, target, body, state, kind, ts, ref) VALUES" +
		"(1, 'alice', 'bob', 'one', 'landed', 'ask', 1, '3f9a0c71be2d'), (2, 'bob', 'alice', 'two', 'landed', 'ask', 1, '3f9a0c00aa11'), " +
		"(3, 'bob', 'carol', 'three', 'landed', 'ask', 1, '3f9a0cffff00')"); err != nil {
		t.Fatal(err)
	}
	code := func(err error) string {
		if e, ok := err.(*rpc.Error); ok {
			return e.Code
		}
		return ""
	}
	for _, c := range []struct {
		name   string
		value  any
		viewer string
		want   int64
		code   string
	}{
		{"an old integer", json.Number("1"), "alice", 1, ""},
		{"the full ref", "3f9a0c71be2d", "alice", 1, ""},
		{"a ref in any case", "3F9A0C00AA11", "alice", 2, ""},
		{"M<n>", "M1", "alice", 1, ""},
		{"m<n>", "m2", "alice", 2, ""},
		{"#<n> is a PR, not a message", "#1", "alice", 0, "bad_request"},
		{"#ref is a PR's mark too", "#3f9a0c00aa11", "alice", 0, "bad_request"},
		{"a unique prefix", "3f9a0c7", "alice", 1, ""},
		{"a prefix two readable requests share", "3f9a0c", "alice", 0, "bad_request"},
		{"one readable among three is no longer ambiguous", "3f9a0c", "carol", 3, ""},
		{"a stranger's ref", "3f9a0cffff00", "alice", 0, "not_found"},
		{"a stranger's integer", json.Number("3"), "alice", 0, "not_found"},
		{"a missing integer", json.Number("99"), "alice", 0, "not_found"},
		{"the founder reads any", "3f9a0cffff00", "human", 3, ""},
		{"too short a prefix", "3f9a0", "alice", 0, "bad_request"},
		{"not hex", "zzzzzz", "alice", 0, "bad_request"},
		{"a boolean", true, "alice", 0, "bad_request"},
	} {
		got, err := Resolve(q, home, c.value, c.viewer, "id")
		if got != c.want || code(err) != c.code {
			t.Errorf("%s: %d %v, want %d %q", c.name, got, err, c.want, c.code)
		}
	}
}

// A message is named M<n>, a bare number or its ref; #<n> is a GitHub PR, refused with the line hl prints.
func TestNameReadsMNAndRefusesHashN(t *testing.T) {
	for _, c := range []struct {
		in   string
		want any
		why  string
	}{
		{"M42", int64(42), ""},
		{" m7 ", int64(7), ""},
		{"42", int64(42), ""},
		{"c0ffee", "c0ffee", ""},
		{"123456789012", "123456789012", ""},
		{"#42", nil, "#42 is PR 42; a message is M42"},
		{"#c0ffee", nil, "#c0ffee: # names a PR; a message is M<n>, or its ref without #"},
		{"Mx", nil, ""},
	} {
		v, why, ok := Name(c.in)
		if v != c.want || why != c.why || ok != (c.want != nil) {
			t.Errorf("%q: %v %q %v", c.in, v, why, ok)
		}
	}
	if Label("42") != "M42" || Label("c0ffee") != "c0ffee" {
		t.Fatal("Label")
	}
}
