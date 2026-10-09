package main

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/protected"
	"github.com/handline/handline/go/internal/sessiontoken"
	"github.com/handline/handline/go/internal/store"
)

// hookRig is a rig with a migrated database, the Go hook switched on unless off, and the test process as the founder.
func hookRig(t *testing.T, on bool) *rig {
	r := agentsRig(t)
	t.Setenv("HANDLINE_TEST_ROOT", r.root)
	t.Setenv("HANDLINE_AGENT", "")
	t.Setenv("OCP_AGENT", "")
	d, err := store.Open(paths.DBFile(r.home))
	if err != nil {
		t.Fatal(err)
	}
	d.Close()
	if on {
		if err := protected.New(r.home).Put("switches", "go_hook", "on"); err != nil {
			t.Fatal(err)
		}
	}
	return r
}

func preToolUse(tool string, input map[string]any) string {
	b, _ := json.Marshal(map[string]any{"hook_event_name": "PreToolUse", "session_id": "s", "tool_name": tool, "tool_input": input})
	return string(b)
}

func TestHookDeniesAsPythonWritesIt(t *testing.T) {
	r := hookRig(t, true)
	out, errs, code := r.runIn(preToolUse("Write", map[string]any{"file_path": "/nowhere/f.py", "content": "x"}), asAgent, "hook", "PreToolUse")
	want := `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": ` +
		`"Write refused: a.one holds no current task, and every change must belong to one (T295). You own no open task. If the ` +
		`founder just told you to do this, run ` + "`hl task begin \\\"<title>\\\"`" + ` to open one; otherwise ask whoever gave you the ` +
		"work: `hl say <them> \\\"please open a task for <this work>\\\"`." + `"}}` + "\n"
	if code != 0 || errs != "" || out != want {
		t.Fatalf("exit %d, err %q\n got  %s\n want %s", code, errs, out, want)
	}
}

func TestHookLetsThroughWithEmptyJSON(t *testing.T) {
	r := hookRig(t, true)
	for name, c := range map[string]struct {
		env   []string
		stdin string
	}{
		"a safe command":   {asAgent, preToolUse("Bash", map[string]any{"command": "ls"})},
		"no agent":         {nil, preToolUse("Write", map[string]any{"file_path": "/nowhere/f.py"})},
		"not JSON":         {asAgent, "{nope"},
		"nothing on stdin": {asAgent, ""},
	} {
		if out, errs, code := r.runIn(c.stdin, c.env, "hook", "PreToolUse"); code != 0 || errs != "" || out != "{}\n" {
			t.Errorf("%s: exit %d, out %q, err %q", name, code, out, errs)
		}
	}
}

func TestHookVouchesForALandCall(t *testing.T) {
	r := hookRig(t, true)
	if !sessiontoken.New(r.home).MintKey("a.one") {
		t.Fatal("no key")
	}
	calls := r.daemon(nil)
	if out, _, _ := r.runIn(preToolUse("Bash", map[string]any{"command": "hl land --pass abc123"}), asAgent, "hook", "PreToolUse"); out != "{}\n" {
		t.Fatalf("out %q", out)
	}
	key := sessiontoken.New(r.home).KeyHeld("a.one")
	got := calls()
	if len(got) != 1 || got[0]["verb"] != "land_authorize" || got[0]["agent"] != "a.one" || got[0]["land_verb"] != "land_pass" ||
		got[0]["sha"] != "abc123" || got[0]["key"] != key {
		t.Fatalf("sent %v", got)
	}
}

func TestHookIsPythonsWhileTheSwitchIsOffAndForAnyOtherEvent(t *testing.T) {
	off := hookRig(t, false)
	if out, _, code := off.runIn(preToolUse("Write", nil), asAgent, "hook", "PreToolUse"); code != 0 || !strings.HasSuffix(out, " hook PreToolUse\n") ||
		!strings.HasPrefix(out, "python ") {
		t.Errorf("off: %q %d", out, code)
	}
	on := hookRig(t, true)
	for _, args := range [][]string{{"hook", "Stop"}, {"hook", "commit"}, {"hook", "PreToolUse", "x"}} {
		if out, _, code := on.runIn("{}", asAgent, args...); code != 0 || !strings.HasSuffix(out, " "+strings.Join(args, " ")+"\n") {
			t.Errorf("%v: %q %d", args, out, code)
		}
	}
	// The event the payload names wins, as in Python, and Python is given the stdin already read.
	stdin := `{"hook_event_name": "PostToolUse"}`
	if out, _, code := on.runIn(stdin, asAgent, "hook", "PreToolUse"); code != 0 || !strings.HasSuffix(out, " hook PreToolUse\n") {
		t.Errorf("named another event: %q %d", out, code)
	}
}
