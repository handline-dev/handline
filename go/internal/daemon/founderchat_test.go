package daemon

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/supervisor"
)

// A Stop after the founder's typed words, over a transcript with the agent's text, puts nothing in the chat; a say does.
func TestATypedTurnsTerminalTextStaysOutOfTheChat(t *testing.T) {
	supervisor.RunInline = true
	t.Cleanup(func() { supervisor.RunInline = false })
	d := start(t, fakeAuth{agent: "bob"}, "")
	if err := os.WriteFile(filepath.Join(d.cfg.Home, "team.yaml"), []byte(tellTeam), 0o644); err != nil {
		t.Fatal(err)
	}
	d.env().FounderTyped = func(a string) bool { return a == "bob" }
	now := time.Now().Unix()
	if _, err := d.db.Exec("INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok, landed_at, typed_at, founder_auth) "+
		"VALUES(7, ?, 'human', 'bob', 'the code, please', 'landed', 'say', 1, ?, ?, 1)", now, now, now); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "s.jsonl")
	var lines []byte
	for _, rec := range []map[string]any{
		{"type": "user", "origin": map[string]any{"kind": "human"}, "message": map[string]any{"content": "the code, please"}},
		{"type": "assistant", "message": map[string]any{"content": []any{map[string]any{"type": "text", "text": "talking to myself"}}}},
		{"type": "system", "subtype": "turn_duration"},
	} {
		b, _ := json.Marshal(rec)
		lines = append(append(lines, b...), '\n')
	}
	if err := os.WriteFile(path, lines, 0o644); err != nil {
		t.Fatal(err)
	}
	c := dial(t, d)
	c.raw(t, hello)
	stop := fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":"hook.record","params":{"agent":"bob","kind":"Stop","data":{"transcript_path":%q},"at":%d,"request_id":"s1"}}`, path, now)
	if r := c.raw(t, stop); r["error"] != nil {
		t.Fatal(r["error"])
	}
	d.env().Wait()
	toFounder := func() (n int) {
		d.db.QueryRow("SELECT COUNT(*) FROM requests WHERE requester = 'bob' AND target = 'human'").Scan(&n)
		return n
	}
	if n := toFounder(); n != 0 {
		t.Fatalf("%d messages to the founder from bob's terminal text", n)
	}
	if r := c.raw(t, `{"jsonrpc":"2.0","id":3,"method":"say","params":{"target":"human","body":"ACK123","no_task":"answer","request_id":"s2"}}`); r["error"] != nil {
		t.Fatal(r["error"])
	}
	if n := toFounder(); n != 1 {
		t.Fatalf("%d messages to the founder after one say", n)
	}
}
