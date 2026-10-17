package reads

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/handoff"
	"github.com/handline/handline/go/internal/paths"
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
	var noNotification []string
	for _, ev := range hooksWanted {
		if ev != "Notification" {
			noNotification = append(noNotification, ev)
		}
	}
	write(noNotification)
	if e.hooksInstalled() {
		t.Fatal("no Notification hook: not installed, or no needs-you card comes from it")
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

func TestSettingsShowTheHandoffPromptAsSetOrTheBuiltInAndAnAgentsOwn(t *testing.T) {
	e := testEnv(t)
	os.MkdirAll(filepath.Join(e.Home, "agents", "ann"), 0o755)
	writeAgentYAML(t, e, "ann", "role: r\nhandoff_prompt: \"ann: {path}\"\n")
	get := func(agent string) map[string]any {
		t.Helper()
		params := map[string]any{}
		if agent != "" {
			params["agent"] = agent
		}
		out, err := e.settingsGet(nil, params)
		if err != nil {
			t.Fatal(err)
		}
		return out.(map[string]any)
	}
	if got := get("")["values"].(map[string]any)[handoff.PromptKey]; got != handoff.AskTemplate {
		t.Fatalf("unset: %q", got)
	}
	os.WriteFile(paths.ConfigFile(e.Home), []byte("handoff_prompt: \"Mine: {path}\"\n"), 0o644)
	if got := get("")["values"].(map[string]any)[handoff.PromptKey]; got != "Mine: {path}" {
		t.Fatalf("set: %q", got)
	}
	if got := get("ann")["overrides"].(map[string]any)[handoff.PromptKey]; got != "ann: {path}" {
		t.Fatalf("ann's own: %q", got)
	}
}

func TestEveryNotificationKindPushesUntilTheFounderTurnsItOff(t *testing.T) {
	e := testEnv(t)
	kinds := []string{"push_prompt_asked", "push_task_done", "push_stall", "push_account_out"}
	values := func() map[string]any {
		t.Helper()
		out, err := e.settingsGet(nil, map[string]any{})
		if err != nil {
			t.Fatal(err)
		}
		return out.(map[string]any)["values"].(map[string]any)
	}
	for _, k := range kinds {
		if values()[k] != true {
			t.Fatalf("%s is off by default", k)
		}
	}
	os.WriteFile(paths.ConfigFile(e.Home), []byte("push_task_done: false\n"), 0o644)
	if v := values(); v["push_task_done"] != false || v["push_stall"] != true {
		t.Fatalf("one kind turned off: %v", v)
	}
}
