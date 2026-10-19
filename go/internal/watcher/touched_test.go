package watcher

import (
	"database/sql"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/store"
)

// The chat and inbox detectors run this query on every look; a plan that scans requests made an idle daemon read the whole table.
func TestTheTouchedQuerySearchesItsIndexAndScansNoRequests(t *testing.T) {
	d, err := store.Open(filepath.Join(t.TempDir(), "plan.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	rows, err := d.Query("EXPLAIN QUERY PLAN "+ChatTouchedSQL, sql.Named("last", 1))
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var plan []string
	for rows.Next() {
		var id, parent, unused int
		var detail string
		rows.Scan(&id, &parent, &unused, &detail)
		plan = append(plan, detail)
	}
	got := strings.Join(plan, "\n")
	if !strings.Contains(got, "INDEX requests_touched_at") {
		t.Fatalf("no requests_touched_at in the plan:\n%s", got)
	}
	if strings.Contains(got, "SCAN requests") {
		t.Fatalf("the plan scans requests:\n%s", got)
	}
}
