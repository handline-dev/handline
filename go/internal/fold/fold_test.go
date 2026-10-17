package fold

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/handoff"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/usage"
)

func journal(t *testing.T, home, agent, day, text string) string {
	t.Helper()
	dir := filepath.Join(paths.AgentDir(home, agent), "journal")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	f := filepath.Join(dir, day+".md")
	if err := os.WriteFile(f, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
	return f
}

func yesterday() *handoff.Digest {
	d := handoff.ParseDigest("")
	d.Agent, d.Date = "bob", "2026-10-09"
	d.Sections["done"] = []string{"shipped the thing"}
	d.Sections["in_progress"] = []string{"the parser", "the port"}
	d.Sections["blocked"] = []string{"waiting on a review"}
	d.Sections["next"] = []string{"the tests"}
	d.Facts = "branch yourname/x"
	return d
}

func TestRolloverCarriesWhatIsOpenAndStartsDoneEmpty(t *testing.T) {
	home := t.TempDir()
	journal(t, home, "bob", "2026-10-09", yesterday().Render())
	e := &Env{Home: home, Today: func() string { return "2026-10-10" }}
	ok, msg := e.Rollover("bob")
	if !ok || msg != "bob: rolled 2026-10-09.md -> 2026-10-10.md (carried 2 in progress, 1 blocked)" {
		t.Fatalf("rollover = %v %q", ok, msg)
	}
	raw, err := os.ReadFile(filepath.Join(paths.AgentDir(home, "bob"), "journal", "2026-10-10.md"))
	if err != nil {
		t.Fatal(err)
	}
	d := handoff.ParseDigest(string(raw))
	if d.Date != "2026-10-10" || len(d.Items("done")) != 0 || len(d.Items("in_progress")) != 2 || len(d.Items("next")) != 1 {
		t.Fatalf("today's digest = %+v", d)
	}
	if !strings.Contains(string(raw), "branch yourname/x") {
		t.Fatalf("the machine record was not carried:\n%s", raw)
	}
}

func TestRolloverLeavesAnExistingDigestAndSaysWhenThereIsNothing(t *testing.T) {
	home := t.TempDir()
	e := &Env{Home: home, Today: func() string { return "2026-10-10" }}
	if ok, msg := e.Rollover("bob"); !ok || msg != "bob: nothing to roll over" {
		t.Fatalf("no journal: %v %q", ok, msg)
	}
	today := journal(t, home, "bob", "2026-10-10", "kept as it is")
	journal(t, home, "bob", "2026-10-09", yesterday().Render())
	if ok, msg := e.Rollover("bob"); !ok || msg != "bob: 2026-10-10 digest already exists" {
		t.Fatalf("existing: %v %q", ok, msg)
	}
	if raw, _ := os.ReadFile(today); string(raw) != "kept as it is" {
		t.Fatalf("today's digest was rewritten: %q", raw)
	}
}

func TestRolloverRollsFromTheLatestEarlierDay(t *testing.T) {
	home := t.TempDir()
	old := yesterday()
	old.Sections["in_progress"] = []string{"stale"}
	journal(t, home, "bob", "2026-10-01", old.Render())
	journal(t, home, "bob", "2026-10-09", yesterday().Render())
	e := &Env{Home: home, Today: func() string { return "2026-10-10" }}
	if _, msg := e.Rollover("bob"); !strings.Contains(msg, "2026-10-09.md -> 2026-10-10.md") {
		t.Fatalf("msg = %q", msg)
	}
}

func foldEnv(t *testing.T) *Env {
	home := t.TempDir()
	d, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	return &Env{Home: home, DB: d, Usage: &usage.Env{Home: home}, Accounts: team.Accounts{Home: home},
		Today: func() string { return "2026-10-10" }}
}

func TestAFoldWithNoAccountToPayKeepsTheCursorAndPublishesTheFacts(t *testing.T) {
	e := foldEnv(t)
	journal(t, e.Home, "bob", "2026-10-09", yesterday().Render())
	db.AddEvent(e.DB, "bob", "PostToolUse", db.KV{K: "tool", V: "Edit"})
	ok, said := e.Fold("bob")
	if ok || !strings.Contains(said, "fold rejected, kept the previous digest and left the cursor at 0") || !strings.Contains(said, "no accounts registered") {
		t.Fatalf("fold = %v %q", ok, said)
	}
	raw, err := os.ReadFile(filepath.Join(paths.AgentDir(e.Home, "bob"), "journal", "2026-10-10.md"))
	if err != nil || !strings.Contains(string(raw), "shipped the thing") || !strings.Contains(string(raw), "events folded: 1") {
		t.Fatalf("degraded digest = %q %v", raw, err)
	}
	if c, _ := db.Cursor(e.DB, "fold:bob"); c != 0 {
		t.Fatalf("cursor moved to %d", c)
	}
	if _, p, ok, _ := db.LastEvent(e.DB, "bob", "fold_failed"); !ok || p == nil {
		t.Fatal("no fold_failed event")
	}
}

func TestAFoldWithNothingNewLeavesALineWhoseRequestIsUnknown(t *testing.T) {
	e := foldEnv(t)
	d := yesterday()
	d.Date = "2026-10-10"
	d.Sections["in_progress"] = []string{"waiting on #999", "step 2 of 7"}
	journal(t, e.Home, "bob", "2026-10-10", d.Render())
	ok, said := e.Fold("bob")
	if !ok || said != "bob: nothing new to fold (cursor at 0)" {
		t.Fatalf("fold = %v %q", ok, said)
	}
	raw, _ := os.ReadFile(filepath.Join(paths.AgentDir(e.Home, "bob"), "journal", "2026-10-10.md"))
	if got := handoff.ParseDigest(string(raw)); len(got.Items("in_progress")) != 2 {
		t.Fatalf("a line naming an unknown request moved: %+v", got.Sections)
	}
}
