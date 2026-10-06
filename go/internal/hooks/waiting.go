package hooks

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
	"unicode"

	"github.com/handline/handline/go/internal/rpc"
)

// An agent stopped at a permission prompt only the founder can answer has one small file, home/waiting/<agent>.json
// (handline/session/waiting.py): written when the prompt is seen, taken away when it is answered.

const shownChars = 120

var inputFields = []string{"command", "file_path", "path", "pattern", "url", "query", "description"}

func waitFile(home, agent string) string { return filepath.Join(home, "waiting", agent+".json") }

// printable is s with every character that is not printable turned into a space, cut to shownChars.
func printable(s string) string {
	return cut(strings.Map(func(r rune) rune {
		if unicode.IsPrint(r) {
			return r
		}
		return ' '
	}, s), shownChars)
}

// summary is the one line that says what the tool was asked to do.
func summary(toolInput any) string {
	text := ""
	switch t := toolInput.(type) {
	case nil:
	case map[string]any:
		found := false
		for _, k := range inputFields {
			if v, ok := t[k].(string); ok && strings.TrimSpace(v) != "" {
				text, found = v, true
				break
			}
		}
		if !found {
			text = rpc.PyJSON(t, true, true)
		}
	case string:
		text = t
	default:
		text = rpc.PyJSON(t, true, true)
	}
	return printable(text)
}

// reason is the tool call's own stated reason, beside the command it explains, or "".
func reason(toolInput any) string {
	m, ok := toolInput.(map[string]any)
	if !ok {
		return ""
	}
	if c, ok := m["command"].(string); !ok || strings.TrimSpace(c) == "" {
		return ""
	}
	if d, ok := m["description"].(string); ok {
		return printable(d)
	}
	return ""
}

// noteWait records that the agent is at a permission prompt for this tool call. The event is already written, so a
// failure here only loses the wait file.
func noteWait(home, agent, tool string, toolInput any, afterEventID int64) {
	data := map[string]any{"tool": tool, "command": summary(toolInput), "reason": reason(toolInput),
		"since": time.Now().Unix(), "after_event_id": afterEventID}
	if m, ok := toolInput.(map[string]any); ok && tool == "AskUserQuestion" {
		data["question_data"] = m
	}
	b, err := json.Marshal(data)
	if err != nil {
		return
	}
	f := waitFile(home, agent)
	if os.MkdirAll(filepath.Dir(f), 0o755) != nil {
		return
	}
	tmp := fmt.Sprintf("%s.%d.tmp", f, os.Getpid())
	if os.WriteFile(tmp, b, 0o644) == nil {
		if os.Rename(tmp, f) != nil {
			os.Remove(tmp)
		}
	}
}

// clearWait: the prompt is answered.
func clearWait(home, agent string) { os.Remove(waitFile(home, agent)) }
