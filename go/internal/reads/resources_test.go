package reads

import (
	"testing"

	"github.com/handline/handline/go/internal/sysres"
)

func TestDaemonStatusServesTheLatestResourcesOrNull(t *testing.T) {
	e := testEnv(t)
	if e.resources() != nil {
		t.Fatal("no sampler serves null")
	}
	e.Resources = func() map[string]any { return nil }
	if e.resources() != nil {
		t.Fatal("before the first sample it is null")
	}
	e.Resources = func() map[string]any { return map[string]any{"at": "x"} }
	if e.resources()["at"] != "x" {
		t.Fatal(e.resources())
	}
}

func TestResourcesNameWhetherStartsAreHeldAndAMachineWithNoAgentMidTurnNeverHolds(t *testing.T) {
	e := testEnv(t)
	e.Resources = func() map[string]any { return map[string]any{"at": "x"} }
	if got := e.resources(); got["start_hold"] != nil {
		t.Fatalf("no machine measure: %v", got)
	}
	e.Machine = func() *sysres.Machine { return &sysres.Machine{Cores: 2, Load1: 40, RAMTotal: 100, RAMFree: 1} }
	got := e.resources()
	if _, has := got["start_hold"]; !has || got["start_hold"] != nil {
		t.Fatalf("over every limit but nothing mid-turn holds nothing: %v", got)
	}
}
