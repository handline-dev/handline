package handoff

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/paths"
)

func (w *world) archiveFiles(agent string) []string {
	w.t.Helper()
	files, _ := filepath.Glob(filepath.Join(paths.HandoffArchiveDir(w.Home, agent), "*.md"))
	return files
}

func TestAnAskedNoteIsArchivedByItsTimeAndSurvivesTheNextAsk(t *testing.T) {
	w := newWorld(t)
	w.agent("a", "IDLE", "alpha", "")
	w.telling.accept = true
	w.event("a", "Stop", "")
	if asked, err := w.Ask("a", "close"); !asked || err != nil {
		t.Fatalf("ask: %v %v", asked, err)
	}
	w.writeNote("a", "deciding: X\nSlowed: hl task done refused twice (T1)\n")
	if _, err := w.Record("a", ""); err != nil {
		t.Fatal(err)
	}
	stamp := time.Unix(w.now, 0).Format(ArchiveLayout)
	first := filepath.Join(paths.HandoffArchiveDir(w.Home, "a"), stamp+".md")
	body, err := os.ReadFile(first)
	if err != nil || !strings.Contains(string(body), "  Slowed: hl task done refused twice (T1)") || !strings.Contains(string(body), "# handoff · a") {
		t.Fatalf("the record is archived as handed, by its time: %v\n%s", err, body)
	}

	w.now += 3600
	w.event("a", "Stop", "")
	if asked, err := w.Ask("a", "close"); !asked || err != nil {
		t.Fatalf("second ask: %v %v", asked, err)
	}
	if _, err := os.Stat(first); err != nil {
		t.Fatal("the next ask removed the archived record")
	}
	w.writeNote("a", "Slowed: none\n")
	if _, err := w.Record("a", ""); err != nil {
		t.Fatal(err)
	}
	if _, err := w.Record("a", ""); err != nil { // no note this time: nothing to archive
		t.Fatal(err)
	}
	if got := w.archiveFiles("a"); len(got) != 2 {
		t.Fatalf("one file per record with a note: %q", got)
	}
}

func TestSlowedLinesComeFromEveryAgentNewestFirstWithoutNone(t *testing.T) {
	home := t.TempDir()
	write := func(agent string, at time.Time, text string) {
		dir := paths.HandoffArchiveDir(home, agent)
		os.MkdirAll(dir, 0o755)
		if err := os.WriteFile(filepath.Join(dir, at.Format(ArchiveLayout)+".md"), []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	now := time.Now()
	write("a", now.Add(-2*time.Hour), "written by the agent just before the close:\n  Slowed: check.sh ran twice (10 min)\n  Slowed: none\n")
	write("b", now.Add(-time.Hour), "  Slowed: check.sh ran twice (10 min)\n  slowed: lower case is not the prefix\n")
	write("b", now.Add(-10*24*time.Hour), "  Slowed: too old for the span\n")
	write("c", now.Add(-time.Hour), "  Slowed: none.\n")

	got, err := SlowedLines(home, "", now.Add(-7*24*time.Hour))
	if err != nil {
		t.Fatal(err)
	}
	var lines []string
	for _, s := range got {
		lines = append(lines, s.Agent+" "+s.Line)
	}
	want := []string{"b Slowed: check.sh ran twice (10 min)", "a Slowed: check.sh ran twice (10 min)"}
	if strings.Join(lines, "\n") != strings.Join(want, "\n") {
		t.Fatalf("got\n%s", strings.Join(lines, "\n"))
	}
	if only, _ := SlowedLines(home, "a", now.Add(-7*24*time.Hour)); len(only) != 1 || only[0].Agent != "a" {
		t.Fatalf("--agent: %v", only)
	}
}

func TestTheSweepRemovesOnlyRecordsOlderThanNinetyDays(t *testing.T) {
	home := t.TempDir()
	dir := paths.HandoffArchiveDir(home, "a")
	os.MkdirAll(dir, 0o755)
	now := time.Now()
	old := filepath.Join(dir, now.Add(-91*24*time.Hour).Format(ArchiveLayout)+".md")
	recent := filepath.Join(dir, now.Add(-89*24*time.Hour).Format(ArchiveLayout)+".md")
	for _, f := range []string{old, recent} {
		os.WriteFile(f, []byte("x\n"), 0o644)
	}
	if err := SweepArchive(home, now); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(old); !os.IsNotExist(err) {
		t.Error("a record past 90 days stayed")
	}
	if _, err := os.Stat(recent); err != nil {
		t.Error("a record inside 90 days went")
	}
}
