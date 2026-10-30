package compose

import (
	"slices"
	"testing"

	"github.com/handline/handline/go/internal/turns"
)

func TestTheBackgroundCheckNamesWhatTheLastStopSaidRuns(t *testing.T) {
	tr := &turns.Tracker{Agent: func(string) string { return "alice" }}
	tr.Hook("alice", "UserPromptSubmit", map[string]any{}, 10)
	tr.Hook("alice", "Stop", map[string]any{"background_tasks": []any{
		map[string]any{"type": "agent", "status": "running", "description": "review the diff"},
		map[string]any{"type": "shell", "status": "running", "command": "go test ./..."},
	}}, 11)
	ports := timeboxPorts{p: &Parts{turns: tr}}
	if got := ports.BgJobs(nil, "alice", 0, 0); !slices.Equal(got, []string{"agent: review the diff", "shell: go test ./..."}) {
		t.Fatalf("named %q", got)
	}
	if got := ports.BgJobs(nil, "alice", 1, 0); !slices.Equal(got, []string{"shell: go test ./..."}) {
		t.Fatalf("with a count of 1: %q", got)
	}
	if got := (timeboxPorts{p: &Parts{}}).BgJobs(nil, "alice", 0, 0); got != nil {
		t.Fatalf("no tracker named %q", got)
	}
}
