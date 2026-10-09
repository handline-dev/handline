package main

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"

	"github.com/handline/handline/go/internal/hlclient"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/protected"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/session"
	"github.com/handline/handline/go/internal/sessiontoken"
	_ "modernc.org/sqlite"
)

// goHookOn is the founder's rollout switch (`hl hooks go on`): only an entry that reads "on" lets the Go hook act, so a
// store that cannot be read leaves every hook to Python.
func goHookOn(home string) bool {
	v, _ := protected.New(home).Get("switches", "go_hook")
	return strings.TrimSpace(v) == "on"
}

// hook is `hl hook PreToolUse` (session/events.record_from_stdin) once the switch is on: the guard chain, then the
// vouch for an `hl land` call. Every other event, and PreToolUse while the switch is off, is Python's. It always exits
// 0, as a hook that fails must never take the session with it.
func hook(c *hlclient.Client, args []string) (int, bool) {
	if len(args) != 1 || args[0] != "PreToolUse" || !goHookOn(c.Home) {
		return 0, false
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
	if ev, given := raw["hook_event_name"]; given && ev != "PreToolUse" {
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
	// guard does.
	d, _ := sql.Open("sqlite", "file:"+paths.DBFile(c.Home)+"?mode=rw&_pragma=busy_timeout(5000)")
	why := session.PreToolUse(d, c.Home, agent, tool, input)
	d.Close()
	if why == "" {
		session.LandVoucher{KeyHeld: sessiontoken.New(c.Home).KeyHeld, Send: c.Legacy}.Authorize(agent, tool, input)
		fmt.Println("{}")
		return 0, true
	}
	fmt.Println(rpc.PyJSON(map[string]any{"hookSpecificOutput": map[string]any{
		"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": why}}, true, true))
	return 0, true
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
