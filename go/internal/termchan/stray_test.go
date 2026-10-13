package termchan

import (
	"encoding/base64"
	"testing"

	"github.com/handline/handline/go/internal/term"
)

// strayHost also lists %9, a pane no agent has.
type strayHost struct{ *fakeHost }

func (strayHost) ListTerminals() map[string]bool { return map[string]bool{"%1": true, "%9": true} }

func TestAPaneNoAgentHasCanBeWatchedWhileTheHostListsItButTakesNoKeys(t *testing.T) {
	r := holdRig(t)
	r.e.Backend = func() term.Backend { return strayHost{r.h} }
	handle, agent, err := r.e.handleOf(map[string]any{"terminal": "%9"})
	if err != nil || handle != "%9" || agent != "" {
		t.Fatalf("a listed pane no agent has: %q %q %v", handle, agent, err)
	}
	if _, _, err := r.e.handleOf(map[string]any{"terminal": "%8"}); err == nil {
		t.Fatal("a pane the host does not list is not_found")
	}
	keys := map[string]any{"terminal": "%9", "data": base64.StdEncoding.EncodeToString([]byte("ls\r"))}
	if _, err := r.e.input(person("pat"), keys); gate(t, err) != "input_holder" {
		t.Fatalf("no agent, no holder: %v", err)
	}
	if len(r.h.sent) != 0 {
		t.Fatal("keys reached a pane no agent has")
	}
}
