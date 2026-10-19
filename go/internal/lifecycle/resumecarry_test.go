package lifecycle

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/team"
)

// A resume on an account that does not hold the session gets the conversation copied over from the one that does, its subagent
// transcripts with it; one already there, or saved nowhere, is left alone.
func TestAResumeOnAnotherAccountCarriesTheSessionOver(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HANDLINE_TEST_ROOT", "")
	for _, a := range []string{"gamma", "beta"} {
		os.MkdirAll(paths.AccountDir(home, a), 0o755)
	}
	gamma, _ := team.AccountConfigDir(home, "gamma")
	beta, _ := team.AccountConfigDir(home, "beta")
	project := filepath.Join(gamma, "projects", "-wt-x")
	os.MkdirAll(filepath.Join(project, "s1", "subagents"), 0o755)
	os.WriteFile(filepath.Join(project, "s1.jsonl"), []byte("{\"turn\":1}\n"), 0o600)
	os.WriteFile(filepath.Join(project, "s1", "subagents", "a.jsonl"), []byte("sub\n"), 0o600)

	for _, extra := range [][]string{{"--resume", "s1"}, {"--resume=s1"}, {"-r", "s1"}} {
		if got := resumeID(extra); got != "s1" {
			t.Fatalf("%v: %q", extra, got)
		}
	}
	if err := carrySession(home, "beta", []string{"--resume", "s1"}); err != nil {
		t.Fatal(err)
	}
	if b, _ := os.ReadFile(filepath.Join(beta, "projects", "-wt-x", "s1.jsonl")); string(b) != "{\"turn\":1}\n" {
		t.Fatalf("the transcript is not carried: %q", b)
	}
	if b, _ := os.ReadFile(filepath.Join(beta, "projects", "-wt-x", "s1", "subagents", "a.jsonl")); string(b) != "sub\n" {
		t.Fatalf("its subagents are not carried: %q", b)
	}
	// It grows on beta; moved back to gamma, the resume takes beta's newer copy, not the one gamma kept.
	later := time.Now().Add(time.Minute)
	onBeta := filepath.Join(beta, "projects", "-wt-x", "s1.jsonl")
	os.WriteFile(onBeta, []byte("{\"turn\":1}\n{\"turn\":2}\n"), 0o600)
	os.Chtimes(onBeta, later, later)
	if err := carrySession(home, "gamma", []string{"--resume", "s1"}); err != nil {
		t.Fatal(err)
	}
	if b, _ := os.ReadFile(filepath.Join(project, "s1.jsonl")); string(b) != "{\"turn\":1}\n{\"turn\":2}\n" {
		t.Fatalf("back on gamma, the turns said on beta are lost: %q", b)
	}
	if err := carrySession(home, "beta", []string{"--resume", "nowhere"}); err != nil || sessionIn(beta, "nowhere") != "" {
		t.Fatalf("a session saved nowhere is left to claude: %v", err)
	}
	os.MkdirAll(paths.AccountDir(home, "delta"), 0o755)
	delta, _ := team.AccountConfigDir(home, "delta")
	if err := carrySession(home, "delta", []string{"--resume", "s*"}); err != nil || isDir(filepath.Join(delta, "projects")) {
		t.Fatalf("an id that is a pattern is not looked for: %v", err)
	}
}
