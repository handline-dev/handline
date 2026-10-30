package term

import (
	"os/exec"
	"testing"
)

// A tmux server that is not running has no windows, which is an answer: the runs in it are over.
func TestPaneRunsOfNoServerIsEmptyNotUnknown(t *testing.T) {
	if _, err := exec.LookPath("tmux"); err != nil {
		t.Skip("no tmux")
	}
	got := Tmux{Home: t.TempDir()}.PaneRuns()
	if got == nil || len(got) != 0 {
		t.Fatalf("%v", got)
	}
}

func TestSignalNumberReadsWhatEveryTmuxCallsASignal(t *testing.T) {
	check := func(want int, names ...string) {
		for _, name := range names {
			if got := signalNumber(name); got != want {
				t.Errorf("%q: %d, want %d", name, got, want)
			}
		}
	}
	check(2, "int", "INT", "SIGINT", "2", "interrupt")
	check(15, "sigterm", "15", "Terminated")
	check(0, "", "nonsense")
}
