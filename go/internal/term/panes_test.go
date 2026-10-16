package term

import "testing"

func TestAPaneIsAtItsShellWhenOnlyTheShellRunsInIt(t *testing.T) {
	for cmd, want := range map[string]bool{"zsh": true, "-zsh": true, "bash": true, "claude": false, "2.1.300": false, "node": false, "": false} {
		if got := (Pane{Command: cmd}).AtShell(); got != want {
			t.Errorf("%q: %v", cmd, got)
		}
	}
}
