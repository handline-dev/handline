package reads

import (
	"os"
	"path/filepath"
	"testing"
)

func TestABoardReadsTheTeamOnce(t *testing.T) {
	e := testEnv(t)
	team := filepath.Join(e.Home, "team.yaml")
	if err := os.WriteFile(team, []byte("teams:\n  lead: [lead, w]\nagents:\n  lead: {}\n  w: {}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	b := &board{}
	if got := b.leadOf(e.Home, "w"); got != "lead" {
		t.Fatalf("lead %q", got)
	}
	if err := os.Remove(team); err != nil {
		t.Fatal(err)
	}
	if got := b.leadOf(e.Home, "w"); got != "lead" {
		t.Fatalf("the team was read again: %q", got)
	}
}
