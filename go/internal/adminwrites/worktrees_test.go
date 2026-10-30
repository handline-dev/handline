package adminwrites

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/reads"
)

func TestSetStatusLabelsAWorktreeAnAgentWorksIn(t *testing.T) {
	r := newRig(t)
	wt, _ := filepath.EvalSymlinks(t.TempDir())
	if _, err := r.e.DB.Exec("INSERT INTO agent_state(agent, state, updated, worktree) VALUES('alice', 'idle', 0, ?)", wt); err != nil {
		t.Fatal(err)
	}
	set := func(status, id string) map[string]any {
		return r.one("worktree.set_status", fmt.Sprintf(`{"path":%q,"status":%q,"request_id":%q}`, wt, status, id))
	}
	if res := r.result(set("in review", "w1")); res["accepted"] != true {
		t.Fatalf("%v", res)
	}
	var got string
	r.e.DB.QueryRow("SELECT value FROM meta WHERE name = ?", reads.StatusLabelPrefix+wt).Scan(&got)
	if got != "in review" {
		t.Fatalf("label %q", got)
	}
	r.result(set("done", "w2"))
	r.e.DB.QueryRow("SELECT value FROM meta WHERE name = ?", reads.StatusLabelPrefix+wt).Scan(&got)
	if got != "done" {
		t.Fatalf("set again: %q", got)
	}
	if r.result(set("done", "w2"))["replayed"] != true {
		t.Fatal("a retry replays")
	}
	for _, bad := range []string{"", strings.Repeat("x", 41), "two\nlines"} {
		if c := r.code(set(bad, "w-"+bad[:min(len(bad), 3)])); c != "bad_request" {
			t.Fatalf("status %q: %s", bad, c)
		}
	}
	if c := r.code(r.one("worktree.set_status", `{"path":"/","status":"done","request_id":"w9"}`)); c != "not_found" {
		t.Fatalf("a path no agent works in: %s", c)
	}
	if _, err := os.Stat(wt); err != nil {
		t.Fatal(err)
	}
}
