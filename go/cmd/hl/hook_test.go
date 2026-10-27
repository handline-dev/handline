package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/protected"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/session"
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
	out, errs, code := r.runIn(preToolUse("Bash", map[string]any{"command": "hl say worker \"`whoami`\""}), asAgent, "hook", "PreToolUse")
	want := `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": ` +
		`"Refused: nothing in this command ran, including any heredoc or file write in it.`
	if code != 0 || errs != "" || !strings.HasPrefix(out, want) || !strings.HasSuffix(out, "\"}}\n") {
		t.Fatalf("exit %d, err %q\n got  %s\n want %s...", code, errs, out, want)
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

// Every hook event is the Go hl's: none reaches Python, whatever the switch says.
func TestHookIsGoForEveryEventAndNeverPython(t *testing.T) {
	r := hookRig(t, false)
	r.daemon(nil)
	for _, args := range [][]string{{"hook", "PreToolUse"}, {"hook", "Stop"}, {"hook", "commit"}, {"hook", "Notification"},
		{"hook", "TeammateIdle"}, {"hook", "PreToolUse", "x"}, {"hook"}} {
		if out, _, code := r.runIn("{}", asAgent, args...); code != 0 || strings.HasPrefix(out, "python ") {
			t.Errorf("%v: %q %d", args, out, code)
		}
	}
	// The event the payload names wins, as in Python.
	guarded := preToolUse("Bash", map[string]any{"command": "hl say worker \"`whoami`\""})
	if out, _, _ := r.runIn(guarded, asAgent, "hook", "PostToolUse"); !strings.Contains(out, `"permissionDecision": "deny"`) {
		t.Errorf("the payload's PreToolUse was not guarded: %q", out)
	}
}

// A Stop is never refused, a current task with no recorded wait included: it is recorded and let through.
func TestAStopIsLetThroughAndRecorded(t *testing.T) {
	r := hookRig(t, false)
	calls := r.daemon(nil)
	d, err := store.Open(paths.DBFile(r.home))
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	for _, q := range []string{
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(7, 'job', 'a.one', 'human', 1)",
		"INSERT INTO agent_state(agent, state, updated, current_task) VALUES('a.one', 'RUNNING', 1, 7)",
	} {
		if _, err := d.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
	if out, _, _ := r.runIn(`{"hook_event_name": "Stop", "session_id": "s1"}`, asAgent, "hook", "Stop"); out != "{}\n" {
		t.Fatalf("refused: %q", out)
	}
	recorded := 0
	for _, c := range calls() {
		if c["method"] == "hook.record" {
			recorded++
		}
	}
	if recorded != 1 {
		t.Fatalf("the stop is recorded: %d", recorded)
	}
}

func TestNotificationReachesTheDaemon(t *testing.T) {
	r := hookRig(t, false)
	calls := r.daemon(nil)
	note := `{"hook_event_name": "Notification", "session_id": "s1", "notification_type": "permission_prompt", "message": "Claude needs your permission"}`
	if out, _, code := r.runIn(note, asAgent, "hook", "Notification"); code != 0 || out != "{}\n" {
		t.Fatalf("%q %d", out, code)
	}
	got := calls()
	if len(got) != 1 || got[0]["method"] != "hook.record" {
		t.Fatalf("calls %v", got)
	}
	if p := got[0]["params"].(map[string]any); p["kind"] != "Notification" || p["data"].(map[string]any)["notification_type"] != "permission_prompt" {
		t.Fatalf("params %v", p)
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

// The PermissionRequest hook is recorded, then returns the founder's answer from permission.ask as the hook's decision; with no answer it
// returns nothing, and the agent's own dialog shows.
func TestThePermissionHookReturnsTheFoundersAnswer(t *testing.T) {
	ask := `{"hook_event_name": "PermissionRequest", "session_id": "s", "tool_name": "Bash", "tool_input": {"command": "rm -rf node_modules"}}`
	for _, c := range []struct{ result, want string }{
		{`{"behavior": "allow"}`, `{"hookSpecificOutput": {"decision": {"behavior": "allow"}, "hookEventName": "PermissionRequest"}}`},
		{`{"behavior": "deny", "message": "The founder said no: use the script"}`,
			`{"hookSpecificOutput": {"decision": {"behavior": "deny", "message": "The founder said no: use the script"}, "hookEventName": "PermissionRequest"}}`},
		{`{"behavior": null}`, `{}`},
		{`error:down`, `{}`},
	} {
		r := hookRig(t, false)
		calls := r.daemon(map[string]string{"permission.ask": c.result})
		out, _, code := r.runIn(ask, asAgent, "hook", "PermissionRequest")
		if code != 0 || out != c.want+"\n" {
			t.Errorf("%s: exit %d\n got  %q\n want %q", c.result, code, out, c.want)
		}
		var methods []string
		for _, m := range calls() {
			methods = append(methods, m["method"].(string))
			if m["method"] == "permission.ask" {
				p := m["params"].(map[string]any)
				if p["tool"] != "Bash" || p["input"].(map[string]any)["command"] != "rm -rf node_modules" {
					t.Errorf("asked with %v", p)
				}
			}
		}
		if !strings.Contains(strings.Join(methods, " "), "hook.record") || !strings.Contains(strings.Join(methods, " "), "permission.ask") {
			t.Errorf("calls %v", methods)
		}
	}
}

func TestAQuestionIsLeftToTheTerminal(t *testing.T) {
	ask := `{"hook_event_name": "PermissionRequest", "session_id": "s", "tool_name": "AskUserQuestion", "tool_input": {"questions": []}}`
	r := hookRig(t, false)
	calls := r.daemon(map[string]string{"permission.ask": `{"behavior": "allow"}`})
	out, _, code := r.runIn(ask, asAgent, "hook", "PermissionRequest")
	if code != 0 || out != "{}\n" {
		t.Errorf("exit %d, got %q", code, out)
	}
	for _, m := range calls() {
		if m["method"] == "permission.ask" {
			t.Errorf("asked the founder: %v", m["params"])
		}
	}
}

// A founder's message typed into the agent's terminal and left unanswered holds its Stop once: never again for that message, again for
// a new one, not for one answered with a say or a Stop the hook already held, and an error lets the Stop through.
func TestAFoundersUnansweredMessageHoldsTheStopOncePerMessage(t *testing.T) {
	r := hookRig(t, false)
	r.daemon(nil)
	d, err := store.Open(paths.DBFile(r.home))
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	exec := func(q string, args ...any) {
		t.Helper()
		if _, err := d.Exec(q, args...); err != nil {
			t.Fatal(err)
		}
	}
	typed := func(landed int64) int64 { // the founder's message, typed into a.one's terminal and landed at landed
		t.Helper()
		rid, err := db.AddRequest(d, db.NewRequest{Requester: db.Human, Target: "a.one", Kind: "say", Body: "how is it going?"})
		if err != nil {
			t.Fatal(err)
		}
		exec("UPDATE requests SET founder_auth = 1, landed_at = ?, state = 'landed' WHERE id = ?", landed, rid)
		return rid
	}
	stopped := func(at int64) { exec("INSERT INTO events(ts, agent, kind) VALUES(?, 'a.one', 'Stop')", at) } // as the daemon records it
	stop := func(active bool) string {
		t.Helper()
		out, _, code := r.runIn(fmt.Sprintf(`{"hook_event_name": "Stop", "session_id": "s1", "stop_hook_active": %v}`, active), asAgent, "hook", "Stop")
		if code != 0 {
			t.Fatalf("exit %d", code)
		}
		return strings.TrimSpace(out)
	}
	held := func(rid int64) string {
		return rpc.PyJSON(map[string]any{"decision": "block", "reason": session.FounderNudgeText(db.MsgName(d, rid))}, true, true)
	}

	first := typed(100)
	if out := stop(false); out != held(first) {
		t.Fatalf("an unanswered message holds the stop: %s", out)
	}
	stopped(101)
	if out := stop(false); out != "{}" {
		t.Fatalf("the same message is never nudged again: %s", out)
	}
	typed(90)
	if out := stop(false); out != "{}" {
		t.Fatalf("a message that landed before the last stop is not this turn's: %s", out)
	}
	second := typed(102)
	if out := stop(false); out != held(second) {
		t.Fatalf("a new message is nudged again: %s", out)
	}
	stopped(103)
	typed(104)
	answer, err := db.AddRequest(d, db.NewRequest{Requester: "a.one", Target: db.Human, Kind: "say", Body: "going well"})
	if err != nil {
		t.Fatal(err)
	}
	exec("UPDATE requests SET ts = 105 WHERE id = ?", answer)
	if out := stop(false); out != "{}" {
		t.Fatalf("an answered message is not nudged: %s", out)
	}
	stopped(106)
	exec("UPDATE requests SET no_work = 'thanks' WHERE id = ?", typed(107))
	exec("INSERT INTO tasks(id, title, owner, opened_by, created_at, from_request) VALUES(8, 'asked', 'a.one', 'human', 1, ?)", typed(108))
	exec("INSERT INTO task_requests(task_id, request_id) VALUES(8, ?)", typed(109))
	exec("UPDATE requests SET founder_auth = 0 WHERE id = ?", typed(109))
	if out := stop(false); out != "{}" {
		t.Fatalf("a no-work mark, a task made from it or linked to it, or a message not typed by the founder is no nudge: %s", out)
	}
	third := typed(110)
	if out := stop(true); out != "{}" {
		t.Fatalf("a stop the hook already held passes: %s", out)
	}
	if out := stop(false); out != held(third) {
		t.Fatalf("the held stop left the message unanswered: %s", out)
	}
	typed(111)
	exec("DROP TABLE task_requests")
	if out := stop(false); out != "{}" {
		t.Fatalf("an error lets the stop through: %s", out)
	}
}
