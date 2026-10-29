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
