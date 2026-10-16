package claude

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"testing"
)

func settingsOf(t *testing.T, h Hooks) map[string]any {
	t.Helper()
	b, err := os.ReadFile(h.Target())
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatal(err)
	}
	return m
}

func writeSettings(t *testing.T, h Hooks, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(h.Target()), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(h.Target(), []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestSafeCommandsMergeIntoTheSettingsOnceAndKeepTheFoundersEntries(t *testing.T) {
	h := Hooks{Home: t.TempDir()}
	if changed, err := h.AllowSafeCommands(); err != nil || !changed {
		t.Fatalf("no file: %v %v", changed, err)
	}
	allow, _ := settingsOf(t, h)["permissions"].(map[string]any)["allow"].([]any)
	if len(allow) != len(SafeCommands()) || !slices.Contains(allow, any("Bash(hl inbox:*)")) || slices.Contains(allow, any("Bash(hl task done:*)")) ||
		slices.Contains(allow, any("Bash(ocp:*)")) {
		t.Fatalf("%v", allow)
	}
	before, _ := os.ReadFile(h.Target())
	if changed, _ := h.AllowSafeCommands(); changed {
		t.Fatal("a second run changed something")
	}
	if after, _ := os.ReadFile(h.Target()); string(after) != string(before) {
		t.Fatal("a second run rewrote the file")
	}
	writeSettings(t, h, `{"model": "x", "permissions": {"allow": ["Bash(npm test:*)", "Bash(tail:*)"], "deny": ["Bash(rm:*)"]}}`)
	if changed, _ := h.AllowSafeCommands(); !changed {
		t.Fatal("an existing file did not change")
	}
	m := settingsOf(t, h)
	allow = m["permissions"].(map[string]any)["allow"].([]any)
	tails := 0
	for _, a := range allow {
		if a == "Bash(tail:*)" {
			tails++
		}
	}
	if allow[0] != "Bash(npm test:*)" || allow[1] != "Bash(tail:*)" || tails != 1 || m["model"] != "x" ||
		len(m["permissions"].(map[string]any)["deny"].([]any)) != 1 {
		t.Fatalf("%v", m)
	}
	writeSettings(t, h, `{"permissions": {"allow": "oops"}}`)
	if changed, _ := h.AllowSafeCommands(); !changed || len(settingsOf(t, h)["permissions"].(map[string]any)["allow"].([]any)) != len(SafeCommands()) {
		t.Fatal("a malformed allow is replaced by ours")
	}
}

func TestHomeWritesReplaceTheEntriesUnderTheHomeAndNameAParentThatDenies(t *testing.T) {
	root := t.TempDir()
	home := filepath.Join(root, "hl")
	if err := os.MkdirAll(filepath.Join(home, "agents"), 0o755); err != nil {
		t.Fatal(err)
	}
	h := Hooks{Home: home}
	writeSettings(t, h, `{"sandbox": {"enabled": true, "filesystem": {"allowWrite": ["/elsewhere", "`+filepath.Join(home, "agents")+`"], `+
		`"denyWrite": ["`+home+`", "`+root+`"]}}, "model": "x"}`)
	changed, blockers, err := h.AllowHomeWrites()
	if err != nil || !changed || len(blockers) != 1 || blockers[0] != root {
		t.Fatalf("%v %v %v", changed, blockers, err)
	}
	fs := settingsOf(t, h)["sandbox"].(map[string]any)["filesystem"].(map[string]any)
	if allow := fs["allowWrite"].([]any); len(allow) != 2 || allow[0] != "/elsewhere" || allow[1] != home {
		t.Fatalf("allow: %v", allow)
	}
	if deny := fs["denyWrite"].([]any); len(deny) != 1 || deny[0] != root {
		t.Fatalf("deny: %v", deny)
	}
	if changed, _, _ := h.AllowHomeWrites(); changed {
		t.Fatal("a second run changed something")
	}
	for _, bad := range []string{`"oops"`, `["x"]`, `{"filesystem": "oops"}`} {
		writeSettings(t, h, `{"sandbox": `+bad+`}`)
		if changed, _, err := h.AllowHomeWrites(); err != nil || !changed {
			t.Errorf("a sandbox block of %s: %v %v", bad, changed, err)
		}
	}
}
