package taskwrites

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/store"
)

func TestATasksReporterEditsItAndTheOwnersManagerMayOnlyReassignIt(t *testing.T) {
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
	if why := edit("shop.a", s("shop.a"), nil, nil); !strings.HasSuffix(why, "or shop.b's manager") {
		t.Fatalf("someone else's agent may not take it: %q", why)
	}
	if why := edit("shop", s("shop.a"), nil, nil); why != "" {
		t.Fatalf("the owner's manager may reassign it: %s", why)
	}
	if why := edit("shop", nil, s("shop"), nil); why == "" {
		t.Fatal("the manager may not change the reporter")
	}
	if why := edit("shop", s("shop.b"), nil, s("both")); why == "" {
		t.Fatal("nor change more than the owner")
	}
	if why := edit("other.c", nil, s("other.c"), nil); why != "" {
		t.Fatalf("the opener may change the reporter: %s", why)
	}
	if owner, reporter, _ := read(); owner != "shop.a" || reporter != "other.c" {
		t.Fatalf("owner %q reporter %q", owner, reporter)
	}
	if why := edit("other", s("shop.b"), nil, nil); !strings.Contains(why, "whoever opened it (other.c) or the founder") {
		t.Fatalf("the old reporter may no longer edit, and a reporter that is the opener is named once: %q", why)
	}
}
