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

func TestSeedRulesWritesEachNewTeamTheDefaultOnce(t *testing.T) {
	home := t.TempDir()
	dir := filepath.Join(home, "shared", "team")
	read := func(name string) (string, bool) {
		b, err := os.ReadFile(filepath.Join(dir, name+".md"))
		return string(b), err == nil
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "own.md"), []byte("our rule\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	tm := &Team{Teams: map[string][]string{"a": {"x"}, "own": {"y"}}}
	if err := SeedRules(home, tm); err != nil {
		t.Fatal(err)
	}
	if got, _ := read("a"); got != DefaultRules {
		t.Errorf("a new team is not seeded: %q", got)
	}
	if got, _ := read("own"); got != "our rule\n" {
		t.Errorf("a team's own rules were overwritten: %q", got)
	}
	os.Remove(filepath.Join(dir, "a.md"))
	tm.Teams["b"] = []string{"z"}
	if err := SeedRules(home, tm); err != nil {
		t.Fatal(err)
	}
	if _, ok := read("a"); ok {
		t.Error("a team whose file was deleted was seeded again")
	}
	if got, _ := read("b"); got != DefaultRules {
		t.Errorf("a team added later is not seeded: %q", got)
	}
}
