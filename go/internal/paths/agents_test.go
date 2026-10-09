package paths

import (
	"os"
	"path/filepath"
	"testing"
)

func TestReadAgentsTellsAnUnreadableListingFromAnEmptyOne(t *testing.T) {
	home := t.TempDir()
	if list, err := ReadAgents(home); err != nil || len(list) != 0 {
		t.Fatalf("no agents directory yet: %v %v", list, err)
	}
	os.MkdirAll(filepath.Join(AgentsDir(home), "a"), 0o755)
	os.WriteFile(filepath.Join(AgentsDir(home), "a", "agent.yaml"), nil, 0o644)
	os.MkdirAll(filepath.Join(AgentsDir(home), "no-yaml"), 0o755)
	if list, err := ReadAgents(home); err != nil || len(list) != 1 || list[0] != "a" {
		t.Fatalf("%v %v", list, err)
	}
	broken := t.TempDir()
	os.WriteFile(AgentsDir(broken), []byte("x"), 0o644) // a file where the directory should be
	if _, err := ReadAgents(broken); err == nil {
		t.Fatal("an agents directory that cannot be read is an error")
	}
}
