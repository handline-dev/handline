package reads

import (
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
)

// A disk too full for builds is one needs-you item on daemon.status, in plain words, until the housekeeping watch takes it away.
func TestAFullDiskIsOneMachineNeedOnTheStatus(t *testing.T) {
	e := testEnv(t)
	if n := needs(t, e); len(n) != 0 {
		t.Fatalf("an item with room: %v", n)
	}
	db.SetCursor(e.DB, "disk_full_since", 1_700_000_000)
	db.SetCursor(e.DB, "disk_full_free", 3<<30)
	n := needs(t, e)
	item, _ := n[0].(map[string]any)
	if len(n) != 1 || item["kind"] != "disk" || !strings.Contains(item["text"].(string), "(3.0 GB free)") || item["since"] == nil {
		t.Fatalf("%v", n)
	}
	db.ClearCursor(e.DB, "disk_full_since")
	if n := needs(t, e); len(n) != 0 {
		t.Fatalf("the item stayed: %v", n)
	}
}

func needs(t *testing.T, e *Env) []any {
	t.Helper()
	n, err := e.machineNeeds()
	if err != nil {
		t.Fatal(err)
	}
	return n
}

// A deferred disk need carries when it returns, and loses it with the deferral.
func TestADeferredDiskNeedCarriesLater(t *testing.T) {
	e := testEnv(t)
	db.SetCursor(e.DB, "disk_full_since", 1_700_000_000)
	db.SetCursor(e.DB, "disk_full_free", 3<<30)
	until := time.Now().Add(time.Hour).Unix()
	if err := db.LaterSet(e.DB, db.LaterMachine, "disk", &until, nil, time.Now().Unix()); err != nil {
		t.Fatal(err)
	}
	item, _ := needs(t, e)[0].(map[string]any)
	if got, _ := item["later"].(map[string]any); got["until"] != ts(until) || len(got) != 1 {
		t.Fatalf("later: %v", item)
	}
	if _, err := db.LaterClear(e.DB, db.LaterMachine, "disk"); err != nil {
		t.Fatal(err)
	}
	if item, _ = needs(t, e)[0].(map[string]any); item["later"] != nil {
		t.Fatalf("later stayed: %v", item)
	}
}
