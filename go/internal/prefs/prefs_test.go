package prefs

import (
	"database/sql"
	"encoding/json"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/storetest"
)

func set(t *testing.T, d *sql.DB, person string, changes map[string]any, agents []string) error {
	t.Helper()
	tx, err := d.Begin()
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	n, err := navOf(tx, person)
	if err != nil {
		t.Fatal(err)
	}
	if err := apply(&n, changes, agents, func(id int64) bool { return taskOpen(tx, id) }); err != nil {
		return err
	}
	if err := save(tx, person, n); err != nil {
		t.Fatal(err)
	}
	return tx.Commit()
}

func TestAPersonsPrefsStartAsTheDefaultsAndEachKeyGivenReplacesItsOwn(t *testing.T) {
	d := storetest.Open(t)
	if n, _ := navOf(d, "human"); n.Layout != "tree" || len(n.Pins) != 0 || n.UnreadOnly || n.Order == nil || len(n.Order) != 0 {
		t.Fatalf("defaults: %+v", n)
	}
	agents := []string{"a", "b"}
	if err := set(t, d, "human", map[string]any{"pins": []any{"b", "a"}, "layout": "flat"}, agents); err != nil {
		t.Fatal(err)
	}
	if err := set(t, d, "human", map[string]any{"unread_only": true, "hidden": []any{"idle"}, "order": []any{"b", "a"}}, agents); err != nil {
		t.Fatal(err)
	}
	n, _ := navOf(d, "human")
	if !slices.Equal(n.Pins, []string{"b", "a"}) || n.Layout != "flat" || !n.UnreadOnly || !slices.Equal(n.Hidden, []string{"idle"}) ||
		!slices.Equal(n.Order, []string{"b", "a"}) {
		t.Fatalf("%+v", n)
	}
}

func TestABadChangeIsRefusedNamingItsFieldAndKeepsNothing(t *testing.T) {
	d := storetest.Open(t)
	for _, c := range []struct {
		changes map[string]any
		field   string
	}{
		{map[string]any{"pins": []any{"ghost"}}, "changes.nav.pins"},
		{map[string]any{"pins": []any{"a", "a"}}, "changes.nav.pins"},
		{map[string]any{"collapsed": []any{"ghost"}}, "changes.nav.collapsed"},
		{map[string]any{"order": []any{"ghost"}}, "changes.nav.order"},
		{map[string]any{"order": []any{"a", "a"}}, "changes.nav.order"},
		{map[string]any{"order": "a"}, "changes.nav.order"},
		{map[string]any{"hidden": []any{"asleep"}}, "changes.nav.hidden"},
		{map[string]any{"layout": "grid"}, "changes.nav.layout"},
		{map[string]any{"unread_only": "yes"}, "changes.nav.unread_only"},
		{map[string]any{"layout": "flat", "colour": "red"}, "changes.nav.colour"},
	} {
		err := set(t, d, "human", c.changes, []string{"a"})
		if err == nil || !strings.Contains(err.Error(), strings.TrimPrefix(c.field, "changes.nav.")) {
			t.Fatalf("%v: %v", c.changes, err)
		}
	}
	if n, _ := navOf(d, "human"); n.Layout != "tree" {
		t.Fatalf("a refused change was kept: %+v", n)
	}
}

func TestARetiredAgentLeavesPinsFoldsAndOrderAndARenamedOneKeepsItsPlace(t *testing.T) {
	d := storetest.Open(t)
	agents := []string{"a", "b", "c"}
	if err := set(t, d, "human", map[string]any{"pins": []any{"a", "b", "c"}, "collapsed": []any{"b"}, "order": []any{"c", "b", "a"}}, agents); err != nil {
		t.Fatal(err)
	}
	if err := set(t, d, "other", map[string]any{"order": []any{"b", "a", "c"}}, agents); err != nil { // the order alone names them
		t.Fatal(err)
	}
	tx, _ := d.Begin()
	if err := Forget(tx, "b"); err != nil {
		t.Fatal(err)
	}
	if err := Rename(tx, "a", "z"); err != nil {
		t.Fatal(err)
	}
	tx.Commit()
	n, _ := navOf(d, "human")
	if !slices.Equal(n.Pins, []string{"z", "c"}) || len(n.Collapsed) != 0 || !slices.Equal(n.Order, []string{"c", "z"}) {
		t.Fatalf("%+v", n)
	}
	if n, _ := navOf(d, "other"); !slices.Equal(n.Order, []string{"z", "c"}) {
		t.Fatalf("an order-only change was not stored: %+v", n)
	}
}

func TestLaterMergesByItemAndNullClearsOne(t *testing.T) {
	d := storetest.Open(t)
	if _, err := d.Exec("INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(4, 'open', 'a', 'b', 1)"); err != nil {
		t.Fatal(err)
	}
	must := func(later map[string]any) {
		t.Helper()
		if err := set(t, d, "human", map[string]any{"later": later}, nil); err != nil {
			t.Fatal(err)
		}
	}
	must(map[string]any{"task:1": map[string]any{"until": "2099-11-01T09:00:00+09:00"}, "comment:7": map[string]any{"after_task": json.Number("4")}})
	must(map[string]any{"machine:disk": map[string]any{"until": "2099-11-02T00:00:00Z"}, "comment:7": nil})
	got, _ := LaterOf(d, "human")
	if len(got) != 2 || got["task:1"] != (Later{Until: "2099-11-01T00:00:00Z"}) || got["machine:disk"] != (Later{Until: "2099-11-02T00:00:00Z"}) {
		t.Fatalf("%+v", got)
	}
	must(map[string]any{"task:1": nil, "machine:disk": nil})
	if n, _ := navOf(d, "human"); n.Later != nil {
		t.Errorf("later kept after the last was cleared: %+v", n.Later)
	}
}

func TestLaterRefusesWhatIsNotADeferral(t *testing.T) {
	d := storetest.Open(t)
	for name, later := range map[string]any{
		"not an object":       []any{},
		"a prompt":            map[string]any{"prompt:fp1": map[string]any{"until": "2099-11-01T00:00:00Z"}},
		"an hr proposal":      map[string]any{"hr:11": map[string]any{"until": "2099-11-01T00:00:00Z"}},
		"a key with no id":    map[string]any{"task:": map[string]any{"after_task": json.Number("1")}},
		"both until and task": map[string]any{"task:1": map[string]any{"until": "2099-11-01T00:00:00Z", "after_task": float64(2)}},
		"neither":             map[string]any{"task:1": map[string]any{}},
		"a time that is not":  map[string]any{"task:1": map[string]any{"until": "tomorrow"}},
		"a task that is not":  map[string]any{"task:1": map[string]any{"after_task": json.Number("1.5")}},
		"a task of zero":      map[string]any{"task:1": map[string]any{"after_task": json.Number("0")}},
	} {
		if err := set(t, d, "human", map[string]any{"later": later}, nil); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
	if n, _ := navOf(d, "human"); n.Later != nil {
		t.Errorf("a refusal left %+v", n.Later)
	}
}

func TestADeferralIsOverWhenItsTimeIsOrItsTaskIsNotOpen(t *testing.T) {
	now := time.Date(2026, 11, 1, 0, 0, 0, 0, time.UTC)
	open := func(id int64) bool { return id == 5 }
	for _, c := range []struct {
		l    Later
		over bool
	}{
		{Later{Until: "2026-11-01T00:00:01Z"}, false},
		{Later{Until: "2026-11-01T00:00:00Z"}, true},
		{Later{Until: "garbled"}, true},
		{Later{AfterTask: 5}, false},
		{Later{AfterTask: 6}, true},
	} {
		if got := c.l.Over(now, open); got != c.over {
			t.Errorf("%+v: over %v, want %v", c.l, got, c.over)
		}
	}
}

func TestASetDropsTheDeferralsThatAreOver(t *testing.T) {
	d := storetest.Open(t)
	if _, err := d.Exec("INSERT INTO tasks(id, title, owner, opened_by, created_at, done_evidence) VALUES(4, 'open', 'a', 'b', 1, NULL), (5, 'done', 'a', 'b', 1, 'x')"); err != nil {
		t.Fatal(err)
	}
	soon := time.Now().Add(time.Hour).UTC().Format(time.RFC3339)
	for _, later := range []map[string]any{
		{"task:1": map[string]any{"until": "2020-01-01T00:00:00Z"}, "comment:2": map[string]any{"until": soon}},
		{"machine:disk": map[string]any{"after_task": json.Number("4")}, "comment:3": map[string]any{"after_task": json.Number("5")}, "task:6": map[string]any{"after_task": json.Number("99")}},
	} {
		if err := set(t, d, "human", map[string]any{"later": later}, nil); err != nil {
			t.Fatal(err)
		}
	}
	got, _ := LaterOf(d, "human")
	if len(got) != 2 || got["comment:2"].Until != soon || got["machine:disk"].AfterTask != 4 {
		t.Fatalf("only a future time and an open task stay: %+v", got)
	}
}
