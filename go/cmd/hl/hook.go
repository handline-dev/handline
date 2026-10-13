package main

import (
	"bytes"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/hlclient"
	"github.com/handline/handline/go/internal/hooks"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/protected"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/session"
	"github.com/handline/handline/go/internal/sessiontoken"
	_ "modernc.org/sqlite"
)

// goSwitchOn is whether the founder switched a part of the Go hl on (`hl go <hook|launch> on`): only an entry that reads
// "on" does, so a store that cannot be read leaves that part to Python.
func goSwitchOn(home, name string) bool {
	v, _ := protected.New(home).Get("switches", name)
	return strings.TrimSpace(v) == "on"
}

// goEvents are the hook events the Go hl answers once the switch is on; every other one is Python's.
var goEvents = map[string]bool{"PreToolUse": true, "PostToolUse": true, "PostToolUseFailure": true}

// hook is `hl hook <event>` (session/events.record_from_stdin) for the events that fire on every tool call, once the
// switch is on. PreToolUse runs the guard chain; PostToolUse and its failure take
// away the wait a permission prompt raised and record the event, and a PostToolUse notes a memory file used and a
// command started in the background. It always exits 0, as a hook that fails must never take the session with it.
func hook(c *hlclient.Client, args []string) (int, bool) {
	if len(args) != 1 || !goEvents[args[0]] || !goSwitchOn(c.Home, "go_hook") {
		return 0, false
	}
	event := args[0]
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
	if ev, given := raw["hook_event_name"]; given && ev != event {
		return pythonWithStdin(append([]string{"hook"}, args...), text), true
	}
	agent := hlclient.Agent()
	if agent == "" || sessiontoken.New(c.Home).Nested(0) {
		fmt.Println("{}")
		return 0, true
	}
	tool, _ := raw["tool_name"].(string)
	input := raw["tool_input"]
	// No migration from a hook: a database this cannot use as it stands lets the edit gate's call through, as a failing
	// guard does, and loses a background job's note.
	d, _ := sql.Open("sqlite", "file:"+paths.DBFile(c.Home)+"?mode=rw&_pragma=busy_timeout(5000)")
	defer d.Close()
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
	why := session.PreToolUse(d, c.Home, agent, tool, input)
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

// pythonWithStdin runs the Python entry on argv with stdin already read, and answers its exit code.
func pythonWithStdin(argv []string, stdin []byte) int {
	line, err := pythonArgv(argv)
	if err != nil {
		fmt.Println("{}")
		return 0
	}
	cmd := exec.Command(line[0], line[1:]...)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = bytes.NewReader(stdin), os.Stdout, os.Stderr
	if err := cmd.Run(); err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			return ee.ExitCode()
		}
		return 0
	}
	return 0
}
