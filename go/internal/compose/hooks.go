package compose

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/handline/handline/go/internal/atomicfile"
	"github.com/handline/handline/go/internal/paths"
)

// A structured session reads only the project's settings (--setting-sources project), so the hooks the shared settings install for a
// terminal agent do not load. This writes the daemon's own, hooks only, for the session to take with --settings: the hooks of ours in the
// shared settings (claude_hooks._is_our_hook) and nothing else - no permissions, no environment, no tokens.

// guardEvent is the shell guard's hook. A session without it would run Bash and file writes unguarded, so it is not started without it.
const guardEvent = "PreToolUse"

// isOurHook is claude_hooks._is_our_hook: `<exe> hook <event>`, the exe being one of ours at any path.
func isOurHook(command any, event string) bool {
	s, ok := command.(string)
	if !ok {
		return false
	}
	parts := strings.Fields(s)
	if len(parts) < 3 || parts[len(parts)-2] != "hook" || parts[len(parts)-1] != event {
		return false
	}
	for _, c := range paths.Commands {
		if filepath.Base(parts[0]) == c {
			return true
		}
	}
	return false
}

// HooksSettings is the settings a structured session of agent is given: the shared settings' hooks that are ours. The shared settings that
// cannot be read, or hold no guard hook of ours, are an error: the session is not started unguarded.
func HooksSettings(shared []byte) (map[string]any, error) {
	var data map[string]any
	if err := json.Unmarshal(shared, &data); err != nil {
		return nil, fmt.Errorf("the shared settings cannot be read: %w", err)
	}
	hooks, _ := data["hooks"].(map[string]any)
	out := map[string]any{}
	for event, groups := range hooks {
		list, _ := groups.([]any)
		var keep []any
		for _, g := range list {
			group, ok := g.(map[string]any)
			if !ok {
				continue
			}
			entries, _ := group["hooks"].([]any)
			var mine []any
			for _, h := range entries {
				if hm, ok := h.(map[string]any); ok && isOurHook(hm["command"], event) {
					mine = append(mine, h)
				}
			}
			if len(mine) == 0 {
				continue
			}
			ng := map[string]any{}
			for k, v := range group {
				ng[k] = v
			}
			ng["hooks"] = mine
			keep = append(keep, ng)
		}
		if len(keep) > 0 {
			out[event] = keep
		}
	}
	if _, ok := out[guardEvent]; !ok {
		return nil, errors.New("the shared settings install no " + guardEvent + " hook of ours (the shell guard); not starting a session without it")
	}
	return map[string]any{"hooks": out}, nil
}

// WriteHooksSettings writes agent's hooks-only settings file under home and returns its path. The file is the daemon's: mode 0600 in a
// directory of 0700, replaced in one step.
func WriteHooksSettings(home, agent string) (string, error) {
	shared, err := os.ReadFile(filepath.Join(home, "shared", ".claude", "settings.json"))
	if err != nil {
		return "", fmt.Errorf("the shared settings cannot be read: %w", err)
	}
	settings, err := HooksSettings(shared)
	if err != nil {
		return "", err
	}
	b, err := json.MarshalIndent(settings, "", "  ")
	if err != nil {
		return "", err
	}
	dir := filepath.Join(home, "structured", agent)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", err
	}
	path := filepath.Join(dir, "settings.json")
	if err := atomicfile.Write(path, string(b)+"\n"); err != nil {
		return "", err
	}
	return path, os.Chmod(path, 0o600)
}
