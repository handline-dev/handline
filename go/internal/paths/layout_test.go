package paths

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestListsAreSortedAndNeedTheirMarker(t *testing.T) {
	home := t.TempDir()
	for _, d := range []string{"agents/b", "agents/a", "agents/nofile", "accounts/z", "accounts/.hidden", "accounts/m"} {
		os.MkdirAll(filepath.Join(home, d), 0o755)
	}
	os.WriteFile(filepath.Join(home, "agents/a/agent.yaml"), nil, 0o644)
	os.WriteFile(filepath.Join(home, "agents/b/agent.yaml"), nil, 0o644)
	os.WriteFile(filepath.Join(home, "accounts/file"), nil, 0o644)
	if got := ListAgents(home); !reflect.DeepEqual(got, []string{"a", "b"}) {
		t.Errorf("%v", got)
	}
	if got := ListAccounts(home); !reflect.DeepEqual(got, []string{"m", "z"}) {
		t.Errorf("%v", got)
	}
	if got := ListAgents(filepath.Join(home, "none")); got == nil || len(got) != 0 {
		t.Errorf("%#v", got)
	}
	os.WriteFile(filepath.Join(home, "accounts/m/.removing"), nil, 0o644)
	if !Removing(home, "m") || Removing(home, "z") {
		t.Error("removing")
	}
}

func TestBothTwinsEachSpelling(t *testing.T) {
	got := Both(map[string]string{"OCP_HOME": "/a", "HANDLINE_AGENT": "w", "OCP_AGENT": "x"})
	want := map[string]string{"OCP_HOME": "/a", "HANDLINE_HOME": "/a", "HANDLINE_AGENT": "w", "OCP_AGENT": "x"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("%v", got)
	}
	env := map[string]string{}
	Setenv(env, "HOME", "/h")
	if env["OCP_HOME"] != "/h" || env["HANDLINE_HOME"] != "/h" {
		t.Errorf("%v", env)
	}
}

func TestFilesUnderTheHome(t *testing.T) {
	if StatusFile("/h", "acct") != "/h/accounts/acct/status.json" || AgentStatusFile("/h", "w") != "/h/agents/w/status.json" ||
		HandoffNoteFile("/h", "w") != "/h/agents/w/handoff.md" || MCPConfig("/h") != "/h/shared/mcp.json" {
		t.Error("layout")
	}
}
