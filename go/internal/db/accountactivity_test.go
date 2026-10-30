package db

import (
	"strings"
	"testing"
)

// The usage poll reads this every tick: it must go through the account's own events, not every turn event of five kinds.
func TestLastAccountActivityReadsTheAccountsIndex(t *testing.T) {
	q := open(t)
	rows, err := q.Query("EXPLAIN QUERY PLAN "+accountActivitySQL, "acct")
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var plan []string
	for rows.Next() {
		var id, parent, unused int
		var detail string
		if err := rows.Scan(&id, &parent, &unused, &detail); err != nil {
			t.Fatal(err)
		}
		plan = append(plan, detail)
	}
	if !strings.Contains(strings.Join(plan, "\n"), "events_account_ts") {
		t.Fatalf("not through events_account_ts: %v", plan)
	}
}
