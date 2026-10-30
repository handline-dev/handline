package reads

import (
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
)

// A disk too full for builds is one needs-you item on daemon.status, in plain words, until the housekeeping watch takes it away.
func TestAFullDiskIsOneMachineNeedOnTheStatus(t *testing.T) {
	e := testEnv(t)
	if n := e.machineNeeds(); len(n) != 0 {
		t.Fatalf("an item with room: %v", n)
	}
	db.SetCursor(e.DB, "disk_full_since", 1_700_000_000)
	db.SetCursor(e.DB, "disk_full_free", 3<<30)
	n := e.machineNeeds()
	item, _ := n[0].(map[string]any)
	if len(n) != 1 || item["kind"] != "disk" || !strings.Contains(item["text"].(string), "(3.0 GB free)") || item["since"] == nil {
		t.Fatalf("%v", n)
	}
	db.ClearCursor(e.DB, "disk_full_since")
	if n := e.machineNeeds(); len(n) != 0 {
		t.Fatalf("the item stayed: %v", n)
	}
}

// The disk item names the worktrees no agent works in, as their sizes were last measured, so the founder knows what can go.
func TestTheDiskItemNamesTheOrphanWorktrees(t *testing.T) {
	e := testEnv(t)
	db.SetCursor(e.DB, "disk_full_since", 1_700_000_000)
	db.SetCursor(e.DB, "disk_full_free", 3<<30)
	if item := e.machineNeeds()[0].(map[string]any); item["orphan_worktrees"] != nil {
		t.Fatalf("no orphans measured: %v", item)
	}
	db.SetMeta(e.DB, "worktree_size:/w/a", "1073741824")
	db.SetMeta(e.DB, "worktree_size:/w/b", "536870912")
	item := e.machineNeeds()[0].(map[string]any)
	o, _ := item["orphan_worktrees"].(map[string]any)
	if o["count"] != int64(2) || o["size_bytes"] != int64(3<<29) || !strings.Contains(item["text"].(string), "2 worktrees that no agent works in take 1.5 GB") {
		t.Fatalf("%v", item)
	}
}
