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
		{"msg_ and the hash, any case", "MSG_3F9A0C00AA11", "alice", 2, ""}, // local-ref-ok: names are the feature under test
		{"msg_ and a unique prefix", "msg_3f9a0c7", "alice", 1, ""},         // local-ref-ok: names are the feature under test
		{"a # names a PR", "#3", "alice", 0, "bad_request"},
		{"so does # and a hash", "#3f9a0c00aa11", "alice", 0, "bad_request"},
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

func TestAHashMarkWhereAMessageIsNamedSaysItIsAPR(t *testing.T) {
	if got := PRRefusal("#42"); got != "#42 is PR 42; a message is named msg_<hash>" {
		t.Fatal(got)
	}
	if got := PRRefusal("#abc"); got != "#abc: a # names a pull request; a message is named msg_<hash>" {
		t.Fatal(got)
	}
}
