package taskwrites

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/store"
)

func TestATasksReporterEditsItAndAnyoneMayReassignIt(t *testing.T) {
	db, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	home := t.TempDir()
	for _, a := range []string{"shop", "shop.a", "shop.b", "other", "other.c"} {
		os.MkdirAll(filepath.Join(home, "agents", a), 0o755)
		os.WriteFile(filepath.Join(home, "agents", a, "agent.yaml"), nil, 0o644)
	}
	os.WriteFile(filepath.Join(home, "team.yaml"), []byte("agents:\n  shop: {}\n  shop.a: {}\n  shop.b: {}\n  other: {}\n  other.c: {}\n"), 0o644)
	if _, err := db.Exec("INSERT INTO tasks(id, title, owner, opened_by, created_at, reporter) VALUES(1, 'for other', 'shop.b', 'other.c', 1, 'other')"); err != nil {
		t.Fatal(err)
	}
	s := func(v string) *string { return &v }
	edit := func(by string, owner, reporter, title *string) string {
		tx, err := db.Begin()
		if err != nil {
			t.Fatal(err)
		}
		defer tx.Commit()
		why, _, err := editTask(tx, 1, by, home, owner, reporter, title, nil, nil, nil)
		if err != nil {
			t.Fatal(err)
		}
		return why
	}
	read := func() (owner, reporter, answers string) {
		tx, _ := db.Begin()
		defer tx.Rollback()
		tk, _ := getTask(tx, 1)
		answers, _ = asker(tx, tk)
		return tk.Owner, tk.reporter(), answers
	}
	if _, _, a := read(); a != "other" {
		t.Fatalf("a set reporter is who the task answers to: %q", a)
	}
	if why := edit("other", nil, nil, s("renamed")); why != "" {
		t.Fatalf("the reporter may edit: %s", why)
	}
	if why := edit("shop.a", s("shop.a"), nil, nil); why != "" {
		t.Fatalf("anyone may take it on: %s", why)
	}
	if why := edit("shop", nil, s("shop"), nil); why != "" {
		t.Fatalf("anyone may change who answers for it: %s", why)
	}
	if why := edit("shop.a", s("shop.b"), nil, s("both")); why == "" {
		t.Fatal("but reshaping it stays its opener's, its reporter's or the founder's")
	}
	if owner, reporter, _ := read(); owner != "shop.a" || reporter != "shop" {
		t.Fatalf("owner %q reporter %q", owner, reporter)
	}
	if why := edit("other", nil, nil, s("again")); !strings.Contains(why, "whoever opened it (other.c), its reporter (shop) or the founder") {
		t.Fatalf("the old reporter may no longer reshape it: %q", why)
	}
	if n := count(t, db, "SELECT COUNT(*) FROM events WHERE kind = 'edit_task' AND agent = 'shop' AND json_extract(payload, '$.reporter.new') = 'shop'"); n != 1 {
		t.Fatalf("each change is kept with who made it: %d", n)
	}
}
