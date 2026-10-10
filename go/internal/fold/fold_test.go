package fold

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/handoff"
	"github.com/handline/handline/go/internal/paths"
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
	d.Facts = "branch buddyron/x"
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
	if !strings.Contains(string(raw), "branch buddyron/x") {
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

// fake is a script standing in for hl: it prints what it was asked and the home it was given, and exits as the first line of its stdin file says.
func fake(t *testing.T, body string) []string {
	t.Helper()
	f := filepath.Join(t.TempDir(), "hl")
	if err := os.WriteFile(f, []byte("#!/bin/sh\n"+body+"\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	return []string{f}
}

func TestFoldRunsHlFoldForTheAgentOnTheHome(t *testing.T) {
	home := t.TempDir()
	e := &Env{Home: home, Cmd: fake(t, `echo "$1 $2 $HANDLINE_HOME $OCP_HOME"`)}
	ok, said := e.Fold("bob")
	if want := "fold bob " + home + " " + home; !ok || said != want {
		t.Fatalf("fold = %v %q, want %q", ok, said, want)
	}
}

func TestAFoldThatFailsIsRejectedWithWhatItSaid(t *testing.T) {
	e := &Env{Home: t.TempDir(), Cmd: fake(t, `echo "no events to fold"; exit 1`)}
	if ok, said := e.Fold("bob"); ok || said != "bob: fold rejected - no events to fold" {
		t.Fatalf("fold = %v %q", ok, said)
	}
	e.Cmd = []string{"/no/such/hl"}
	if ok, said := e.Fold("bob"); ok || !strings.HasPrefix(said, "bob: fold rejected - ") {
		t.Fatalf("missing hl = %v %q", ok, said)
	}
}

func TestAFoldPastItsTimeoutIsRejectedAndStopped(t *testing.T) {
	e := &Env{Home: t.TempDir(), Cmd: fake(t, `exec sleep 30`), Timeout: 200 * time.Millisecond}
	start := time.Now()
	ok, said := e.Fold("bob")
	if ok || !strings.Contains(said, "timed out") || time.Since(start) > 5*time.Second {
		t.Fatalf("fold = %v %q after %s", ok, said, time.Since(start))
	}
}
