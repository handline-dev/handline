package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
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
		`"Write refused: a.one holds no current task, and every change must belong to one (T295). You own no open task. If the ` + // local-ref-ok: Python's words
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

func postToolUse(event, tool string, input map[string]any, more map[string]any) string {
	m := map[string]any{"hook_event_name": event, "session_id": "s", "tool_name": tool, "tool_input": input}
	for k, v := range more {
		m[k] = v
	}
	b, _ := json.Marshal(m)
	return string(b)
}

func TestPostToolUseRecordsClearsItsWaitAndNotesWhatItUsed(t *testing.T) {
	r := hookRig(t, true)
	calls := r.daemon(nil)
	wait := filepath.Join(r.home, "waiting", "a.one.json")
	if err := os.MkdirAll(filepath.Dir(wait), 0o755); err != nil {
		t.Fatal(err)
	}
	mem := filepath.Join(paths.AgentDir(r.home, "a.one"), "memory")
	if err := os.MkdirAll(mem, 0o755); err != nil {
		t.Fatal(err)
	}
	// Another tool finishing is not an answer to the prompt the Bash call raised; that call finishing is.
	held := `{"tool": "Bash", "command": "make", "since": 5}`
	if err := os.WriteFile(wait, []byte(held), 0o644); err != nil {
		t.Fatal(err)
	}
	read := postToolUse("PostToolUse", "Read", map[string]any{"file_path": filepath.Join(mem, "topic.md")}, nil)
	if out, errs, code := r.runIn(read, asAgent, "hook", "PostToolUse"); code != 0 || errs != "" || out != "{}\n" {
		t.Fatalf("read: %q %q %d", out, errs, code)
	}
	if _, err := os.Stat(wait); err != nil {
		t.Fatal("another tool's call took the wait away")
	}
	use, err := os.ReadFile(filepath.Join(paths.AgentDir(r.home, "a.one"), "memory-use.json"))
	if err != nil || !regexp.MustCompile(`^\{\n "topic\.md": \d+\n\}\n$`).Match(use) {
		t.Fatalf("memory use %q %v", use, err)
	}
	bg := postToolUse("PostToolUse", "Bash", map[string]any{"command": "make", "run_in_background": true},
		map[string]any{"tool_response": "Command running in background with ID: b7x. Output is being written to: /tmp/s/tasks/b7x.output", "tool_use_id": "tu1"})
	if out, _, code := r.runIn(bg, asAgent, "hook", "PostToolUse"); code != 0 || out != "{}\n" {
		t.Fatalf("bg: %q %d", out, code)
	}
	if _, err := os.Stat(wait); err == nil {
		t.Fatal("the call that raised the wait finished and it stayed")
	}
	got := calls()
	if len(got) != 2 || got[0]["method"] != "hook.record" {
		t.Fatalf("calls %v", got)
	}
	p := got[1]["params"].(map[string]any)
	if p["agent"] != "a.one" || p["kind"] != "PostToolUse" || p["request_id"] == "" || p["data"].(map[string]any)["tool_use_id"] != "tu1" {
		t.Fatalf("params %v", p)
	}
	d, err := store.Open(paths.DBFile(r.home))
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	var payload string
	if err := d.QueryRow("SELECT payload FROM events WHERE agent = 'a.one' AND kind = 'background_started'").Scan(&payload); err != nil ||
		payload != `{"command": "make", "job": "b7x", "output": "/tmp/s/tasks/b7x.output", "session": "s", "tool_use_id": "tu1"}` {
		t.Fatalf("background %q %v", payload, err)
	}
}

func TestPostToolUseSpoolsWhenTheDaemonDoesNotAnswer(t *testing.T) {
	r := hookRig(t, true)
	in := postToolUse("PostToolUseFailure", "Read", map[string]any{"file_path": "/x"}, nil)
	if out, _, code := r.runIn(in, asAgent, "hook", "PostToolUseFailure"); code != 0 || out != "{}\n" {
		t.Fatalf("%q %d", out, code)
	}
	b, err := os.ReadFile(filepath.Join(r.home, "hook-spool.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	var line map[string]any
	if json.Unmarshal(b, &line) != nil || line["agent"] != "a.one" || line["kind"] != "PostToolUseFailure" || line["request_id"] == "" ||
		line["data"].(map[string]any)["tool_name"] != "Read" || !strings.HasSuffix(string(b), "}\n") {
		t.Fatalf("spooled %q", b)
	}
}
