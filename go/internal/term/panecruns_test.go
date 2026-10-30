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
	for in, want := range map[string]int{"int": 2, "INT": 2, "SIGINT": 2, "sigterm": 15, "2": 2, "15": 15, "interrupt": 2, "Terminated": 15, "": 0, "nonsense": 0} {
		if got := signalNumber(in); got != want {
			t.Errorf("%q: %d, want %d", in, got, want)
		}
	}
}
