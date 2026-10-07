package sealed

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/yamlcompat"
)

func setup(t *testing.T) *Seals {
	t.Setenv("HANDLINE_TEST_ROOT", t.TempDir())
	t.Setenv("OCP_AGENT", "")
	t.Setenv("HANDLINE_AGENT", "")
	home := filepath.Join(t.TempDir(), "home")
	if err := os.MkdirAll(filepath.Join(home, "agents", "worker"), 0o755); err != nil {
		t.Fatal(err)
	}
	os.WriteFile(filepath.Join(home, "agents", "worker", "agent.yaml"), []byte("model: x\n"), 0o644)
	os.WriteFile(filepath.Join(home, "team.yaml"), []byte("agents: {}\n"), 0o644)
	return New(home)
}

func TestTheFirstFounderReadSealsWhatIsThereAndAChangeIsRefused(t *testing.T) {
	s := setup(t)
	if st := s.State("team", "agents: {}\n"); st != "legacy" {
		t.Fatalf("%s", st)
	}
	if err := s.Check("team", "agents: {}\n", "team.yaml"); err != nil {
		t.Fatal(err)
	}
	if st := s.State(AgentKey("worker"), "model: x\n"); st != "ok" {
		t.Fatalf("the first read seals every file: %s", st)
	}
	err := s.Check("team", "agents: {x: 1}\n", "team.yaml")
	var ce *yamlcompat.ConfigError
	if !errors.As(err, &ce) || !strings.Contains(err.Error(), "changed outside the founder's path") {
		t.Fatalf("%v", err)
	}
	// After seals began a file nothing sealed is refused: a folder an agent planted is never adopted.
	if err := s.Check(AgentKey("planted"), "x: 1\n", "planted/agent.yaml"); err == nil || !strings.Contains(err.Error(), "never sealed") {
		t.Fatalf("%v", err)
	}
}

func TestObservingSealsNothing(t *testing.T) {
	s := setup(t)
	s.Observing(func() {
		if err := s.Check("team", "agents: {}\n", "team.yaml"); err != nil {
			t.Fatal(err)
		}
	})
	if s.isMigrated() {
		t.Fatal("hl doctor sealed")
	}
}

func TestSealEverythingNamesWhatItAdopts(t *testing.T) {
	s := setup(t)
	got, err := s.SealEverything()
	if err != nil || len(got) != 2 || got[0] != (Adopted{"team.yaml", "new"}) {
		t.Fatalf("%v %v", got, err)
	}
	os.WriteFile(filepath.Join(s.Home, "team.yaml"), []byte("agents: {a: 1}\n"), 0o644)
	got, _ = s.SealEverything()
	if len(got) != 1 || got[0] != (Adopted{"team.yaml", "changed"}) {
		t.Fatalf("%v", got)
	}
}

func TestAnAgentSessionSealsNothing(t *testing.T) {
	s := setup(t)
	t.Setenv("OCP_AGENT", "worker")
	t.Setenv("HANDLINE_AGENT", "worker")
	if s.RequireFounder() == nil || s.Seal("team", "x") == nil {
		t.Fatal("a session sealed")
	}
	if err := s.Check("team", "agents: {}\n", "team.yaml"); err != nil {
		t.Fatalf("legacy is read as it is, not sealed: %v", err)
	}
	if s.isMigrated() {
		t.Fatal("a session began the seals")
	}
}

func TestFollowKeepsTheSealOnlyWhileItHeldBefore(t *testing.T) {
	s := setup(t)
	s.SealEverything()
	s.Follow("team", "agents: {}\n", "agents: {b: 1}\n")
	if s.State("team", "agents: {b: 1}\n") != "ok" {
		t.Fatal("the seal did not follow")
	}
	s.Follow("team", "something else\n", "agents: {c: 1}\n")
	if s.State("team", "agents: {c: 1}\n") == "ok" {
		t.Fatal("a seal followed a file it was not holding")
	}
}
