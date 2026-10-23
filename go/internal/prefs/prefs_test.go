package prefs

import (
	"database/sql"
	"slices"
	"strings"
	"testing"

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
	if err := apply(&n, changes, agents); err != nil {
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
}
