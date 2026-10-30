package watcher

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/reads"
)

// Each kind of item is told as it joins the count and as it leaves it, with the count after it; an item deferred leaves, and comes back.
func TestForYouTellsEachKindOpeningAndClosingWithTheCount(t *testing.T) {
	d, l := rig(t)
	home := t.TempDir()
	w := &ForYou{Reads: &reads.Env{DB: d, Home: home}}
	told := 0
	scan := func() (out []string) {
		t.Helper()
		if err := w.Scan(d, l.Append); err != nil {
			t.Fatal(err)
		}
		got, err := l.Since(0, []string{"for_you"}, "", 100)
		if err != nil {
			t.Fatal(err)
		}
		for _, e := range got[told:] {
			var data map[string]any
			if err := json.Unmarshal(e.Data, &data); err != nil {
				t.Fatal(err)
			}
			if item, ok := data["item"].(map[string]any); ok {
				out = append(out, fmt.Sprintf("%s %s/%s %v", e.Type, item["kind"], item["id"], data["count"]))
			} else {
				out = append(out, fmt.Sprintf("%s %s/%s %v", e.Type, data["kind"], data["id"], data["count"]))
			}
		}
		told = len(got)
		return out
	}
	step := func(want string, q string, args ...any) {
		t.Helper()
		if q != "" {
			exec(t, d, q, args...)
		}
		if got := scan(); len(got) != 1 || got[0] != want {
			t.Fatalf("want %q, got %q", want, got)
		}
	}
	if got := scan(); len(got) != 0 {
		t.Fatalf("a first look told %v", got)
	}
	now := time.Now().Unix()
	exec(t, d, "INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by) VALUES(2, 'other', 'w', 'boss', 1, 'boss')")
	exec(t, d, "INSERT INTO task_memos(id, task_id, author, body, created_at) VALUES(7, 2, 'w', '@human?', 1)")

	step("for_you.opened task/1 1", "INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by) VALUES(1, 'pick', 'human', 'boss', 1, 'boss')")
	step("for_you.opened comment/7 2", "INSERT INTO requests(id, ref, ts, requester, target, body, state, memo_id, edge_ok) VALUES(70, 'aaaaaaaaaaa7', 1, 'w', 'human', '@human?', 'queued', 7, 1)")
	step("for_you.opened prompt/fp1 3", "INSERT INTO prompt_blocks(agent, kind, fingerprint, title, panel, detail, first_seen, last_seen) VALUES('bob', 'permission', 'fp1', 't', '[]', '{}', ?, ?)", now, now)
	if err := os.MkdirAll(filepath.Join(home, "hr"), 0o755); err != nil {
		t.Fatal(err)
	}
	hr := "id: \"0000000b\"\nkind: retire\nagent: old\nrequested_by: boss\nwhy: \"idle\"\ncreated: 5\nstate: %s\n"
	put := func(state string) {
		if err := os.WriteFile(filepath.Join(home, "hr", "0000000b.yaml"), []byte(fmt.Sprintf(hr, state)), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	put("proposed")
	step("for_you.opened hr/11 4", "")
	step("for_you.opened machine/disk 5", "INSERT INTO cursors(name, row_id, updated) VALUES('disk_full_since', 9, 9)")

	// deferred: it leaves the count; back: it joins again
	until := now + 3600
	if err := db.LaterSet(d, db.LaterTask, "1", &until, nil, now); err != nil {
		t.Fatal(err)
	}
	step("for_you.closed task/1 4", "")
	if _, err := db.LaterClear(d, db.LaterTask, "1"); err != nil {
		t.Fatal(err)
	}
	step("for_you.opened task/1 5", "")

	step("for_you.closed task/1 4", "UPDATE tasks SET done_evidence = 'x', done_at = 9 WHERE id = 1")
	step("for_you.closed comment/7 3", "UPDATE requests SET state = 'landed' WHERE id = 70")
	step("for_you.closed prompt/fp1 2", "UPDATE prompt_blocks SET gone_at = 1")
	put("rejected")
	step("for_you.closed hr/11 1", "")
	step("for_you.closed machine/disk 0", "DELETE FROM cursors WHERE name = 'disk_full_since'")
	if got := scan(); len(got) != 0 {
		t.Fatalf("told again: %v", got)
	}
}
