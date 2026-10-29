package main

import (
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/hlclient"
	"github.com/handline/handline/go/internal/hooks"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/session"
	"github.com/handline/handline/go/internal/sessiontoken"
	"github.com/handline/handline/go/internal/usage"
	_ "modernc.org/sqlite"
)

// captured are the hook events recorded as they come (events.CAPTURED, without TeammateIdle, which is no longer kept). Notification is
// installed for permission_prompt only, and the daemon raises the needs-you card from it (-notification-needs-you).
var captured = map[string]bool{"SessionStart": true, "SessionEnd": true, "Stop": true, "StopFailure": true, "SubagentStop": true,
	"PostCompact": true, "UserPromptSubmit": true, "PermissionRequest": true, "Notification": true}

// collected are the moments the session's own status snapshot is pulled again (events.COLLECT_EVENTS): they fire on a turn's edges and
// carry the cwd the transcript is found under. SessionEnd is not one: a snapshot written as a session exits would call it fresh.
var collected = map[string]bool{"SessionStart": true, "UserPromptSubmit": true, "Stop": true, "StopFailure": true, "PostCompact": true}

// hook is `hl hook <event>` (session/events.record_from_stdin). PreToolUse runs the guard chain; PostToolUse and its failure take away the
// wait a permission prompt raised and record the event, and a PostToolUse notes a memory file used and a command started in the background;
// the captured events are recorded, and the turn-edge ones pull the session's status snapshot. It always exits 0,
// as a hook that fails must never take the session with it.
func hook(c *hlclient.Client, args []string) (int, bool) {
	event := ""
	if len(args) > 0 {
		event = args[0]
	}
	defer func() {
		if recover() != nil { // a PreToolUse hook that fails with exit 2 blocks the call: never that
			fmt.Println("{}")
			os.Exit(0)
		}
	}()
	text, _ := io.ReadAll(os.Stdin)
	var raw map[string]any
	if strings.TrimSpace(string(text)) != "" {
		if json.Unmarshal(text, &raw) != nil {
			raw = nil
		}
	}
	if ev, _ := raw["hook_event_name"].(string); ev != "" {
		event = ev // the payload's own name, as Python took it
	}
	agent := hlclient.Agent()
	if agent == "" || sessiontoken.New(c.Home).Nested(0) {
		fmt.Println("{}")
		return 0, true
	}
	tool, _ := raw["tool_name"].(string)
	input := raw["tool_input"]
	// No migration from a hook: a database this cannot use as it stands loses a background job's note.
	d, _ := sql.Open("sqlite", "file:"+paths.DBFile(c.Home)+"?mode=rw&_pragma=busy_timeout(5000)")
	defer d.Close()
	if event == "PermissionRequest" {
		record(c, agent, event, raw)
		fmt.Println(askPermission(c, tool, input))
		return 0, true
	}
	if event != "PreToolUse" && event != "PostToolUse" && event != "PostToolUseFailure" {
		return capture(c, d, agent, event, raw), true
	}
	if event != "PreToolUse" {
		env := &session.Env{DB: d, Home: c.Home}
		switch name := raw["tool_name"].(type) {
		case nil: // no tool named: any prompt is answered
			env.ClearWait(agent)
		case string:
			env.ClearWaitFor(agent, name, input)
		}
		record(c, agent, event, raw)
		if event == "PostToolUse" {
			_ = session.NoteMemoryUse(c.Home, agent, tool, input, time.Now().Unix())
			_ = env.NoteBackground(agent, raw)
		}
		fmt.Println("{}")
		return 0, true
	}
	why := session.Guard(tool, input)
	if why == "" {
		fmt.Println("{}")
		return 0, true
	}
	fmt.Println(rpc.PyJSON(map[string]any{"hookSpecificOutput": map[string]any{
		"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": why}}, true, true))
	return 0, true
}

// record hands the event to the daemon, which writes it down and decides what it means for the agent's state; with the
// daemon not answering within 2s it goes to the spool, which only records (events._record). Nothing here starts one.
func record(c *hlclient.Client, agent, event string, raw map[string]any) {
	if raw == nil {
		raw = map[string]any{}
	}
	rid, at := newRequestID(), time.Now().Unix()
	quick := &hlclient.Client{Home: c.Home, Dial: hlclient.Dialer(c.Home, 2*time.Second),
		Kick: func() error { return errors.New("a hook starts no daemon") }, As: "hook", Deadline: 2 * time.Second}
	if _, err := quick.Call("hook.record", map[string]any{"agent": agent, "kind": event, "at": at, "data": raw, "request_id": rid}); err != nil {
		_ = hooks.AppendSpool(c.Home, agent, event, raw, rid, at)
	}
}

func newRequestID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

// permissionWaitS bounds the permission.ask call: the daemon holds it up to an hour (prompts.HookWaitS), and a little more covers the
// reply's way back. The hook's own timeout in settings is longer still.
const permissionWaitS = 3600 + 30

// askPermission is the PermissionRequest hook's output: the founder's answer through permission.ask, or {} when there is none (the daemon
// is down, the hour passed, or the input is not an object), and the agent's own dialog shows in its terminal. An AskUserQuestion is
// never asked: an allow would carry no answers.
func askPermission(c *hlclient.Client, tool string, input any) string {
	in, ok := input.(map[string]any)
	if tool == "" || tool == "AskUserQuestion" || !ok {
		return "{}"
	}
	waiting := *c
	waiting.Deadline = permissionWaitS * time.Second
	res, err := waiting.Call("permission.ask", map[string]any{"tool": tool, "input": in})
	behavior, _ := res["behavior"].(string)
	if err != nil || behavior == "" {
		return "{}"
	}
	decision := map[string]any{"behavior": behavior}
	if msg, _ := res["message"].(string); behavior == "deny" && msg != "" {
		decision["message"] = msg
	}
	return rpc.PyJSON(map[string]any{"hookSpecificOutput": map[string]any{"hookEventName": "PermissionRequest", "decision": decision}}, true, true)
}

// capture is the rest of record_from_stdin: the record of an event that is kept and the status snapshot on a turn's edge. Anything else is
// answered and left alone.
func capture(c *hlclient.Client, d *sql.DB, agent, event string, raw map[string]any) int {
	if !captured[event] {
		fmt.Println("{}")
		return 0
	}
	out := "{}"
	if event == "Stop" {
		out = founderNudge(&session.Env{DB: d, Home: c.Home}, agent, raw) // before the record, so the last Stop recorded is the turn before
	}
	record(c, agent, event, raw)
	if collected[event] {
		cwd, _ := raw["cwd"].(string)
		if cwd == "" {
			cwd, _ = os.Getwd()
		}
		sid, _ := raw["session_id"].(string)
		usage.CollectSnapshot(c.Home, agent, cwd, sid)
	}
	fmt.Println(out)
	return 0
}

// founderNudge is the Stop hook's output: a block, once per message, when a founder's message typed in this turn has no answer
// (session.UnansweredFounder), else {}. A Stop the hook already held (stop_hook_active) and any error pass.
func founderNudge(env *session.Env, agent string, raw map[string]any) (out string) {
	defer func() {
		if recover() != nil {
			out = "{}"
		}
	}()
	if active, _ := raw["stop_hook_active"].(bool); active {
		return "{}"
	}
	rid, err := env.UnansweredFounder(agent)
	if err != nil || rid == 0 {
		return "{}"
	}
	if first, err := env.NoteFounderNudged(agent, rid); err != nil || !first {
		return "{}"
	}
	return rpc.PyJSON(map[string]any{"decision": "block", "reason": session.FounderNudgeText(db.MsgName(env.DB, rid))}, true, true)
}
