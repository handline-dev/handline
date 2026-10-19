package reads

import "testing"

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
