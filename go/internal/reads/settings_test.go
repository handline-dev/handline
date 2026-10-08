package reads

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestIsOurHookRecognisesTheCommandWhateverPathItWasWrittenWith(t *testing.T) {
	for command, want := range map[string]bool{
		"/usr/local/bin/hl hook Stop": true, "ocp hook Stop": true, "/x/handline hook Stop": true,
		"hl hook SessionStart": false, "echo hook Stop": false, "hl Stop": false, "hook Stop": false,
	} {
		if got := isOurHook(command, "Stop"); got != want {
			t.Errorf("%q: %v", command, got)
		}
	}
	if isOurHook(42, "Stop") {
		t.Error("a command that is not text is not ours")
	}
}

func TestHooksInstalledNeedsOneOfOursForEveryWantedEvent(t *testing.T) {
	home := t.TempDir()
	e := &Env{Home: home}
	if e.hooksInstalled() {
		t.Fatal("no settings file: not installed")
	}
	write := func(events []string) {
		hooks := []string{}
		for _, ev := range events {
			hooks = append(hooks, `"`+ev+`": [{"hooks": [{"type": "command", "command": "/p/hl hook `+ev+`"}]}]`)
		}
		dir := filepath.Join(home, "shared", ".claude")
		os.MkdirAll(dir, 0o755)
		os.WriteFile(filepath.Join(dir, "settings.json"), []byte(`{"hooks": {`+strings.Join(hooks, ",")+`}}`), 0o644)
	}
	write(hooksWanted[1:])
	if e.hooksInstalled() {
		t.Fatal("one event missing: not installed")
	}
	write(hooksWanted)
	if !e.hooksInstalled() {
		t.Fatal("every event has our entry: installed")
	}
	os.WriteFile(filepath.Join(home, "shared", ".claude", "settings.json"), []byte("{not json"), 0o644)
	if e.hooksInstalled() {
		t.Fatal("a file that cannot be read is not installed")
	}
}
