package team

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestTeamRulesAreTheTeamsTheAgentIsInWithText(t *testing.T) {
	home := t.TempDir()
	dir := filepath.Join(home, "shared", "team")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	for name, text := range map[string]string{"zeta": "  last\n", "alpha": "\nfirst rule\n", "empty": " \n", "other": "not mine"} {
		if err := os.WriteFile(filepath.Join(dir, name+".md"), []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	tm := &Team{Teams: map[string][]string{"zeta": {"a"}, "alpha": {"b", "a"}, "empty": {"a"}, "other": {"b"}, "nofile": {"a"}}}
	want := []string{"# Rules for team alpha\nfirst rule", "# Rules for team zeta\nlast"}
	if got := Rules(home, "a", tm); !reflect.DeepEqual(got, want) {
		t.Errorf("got %q", got)
	}
	if got := Rules(home, "a", nil); got != nil {
		t.Errorf("no routing: %q", got)
	}
}
