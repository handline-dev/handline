package session

import (
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/textsafe"
)

// The memory index's budget and its stale age (team/memory.py).
const (
	memoryIndex   = "MEMORY.md"
	indexBytes    = 2048
	indexEntries  = 20
	staleDays     = 28
	staleListed   = 8
	memoryUseFile = "memory-use.json"
)

func memoryDir(home, agent string) string {
	return filepath.Join(paths.AgentDir(home, agent), "memory")
}

// memoryRel is the path a tool call names, relative to the agent's memory folder; ok false when it names something else
// (memoryguard._relative).
func memoryRel(home, agent string, input map[string]any) (string, bool) {
	path, _ := input["file_path"].(string)
	if path == "" {
		return "", false
	}
	root, target := paths.Realpath(memoryDir(home, agent)), paths.Realpath(path)
	if !strings.HasPrefix(target, root+"/") {
		return "", false
	}
	return target[len(root)+1:], true
}

// MemoryGuard is the reason to refuse a Write, Edit or MultiEdit that would leave the agent's memory index over its byte
// or entry budget in a dimension where it also grew, "" to let it through (memoryguard.guard). A call that shrinks the
// index passes even while it is over, since that is how it gets back under.
func MemoryGuard(home, agent, tool string, toolInput any) string {
	input, ok := toolInput.(map[string]any)
	if !ok || (tool != "Write" && tool != "Edit" && tool != "MultiEdit") {
		return ""
	}
	if rel, ok := memoryRel(home, agent, input); !ok || rel != memoryIndex {
		return ""
	}
	current := ""
	if b, err := os.ReadFile(filepath.Join(memoryDir(home, agent), memoryIndex)); err == nil {
		if !utf8.Valid(b) {
			return "" // Python's read raises here, and its hook lets the call through
		}
		current = universalNewlines(string(b))
	}
	result, ok := wouldBe(current, tool, input)
	if !ok {
		return ""
	}
	wasN, nowN := entries(current), entries(result)
	if !((len(result) > indexBytes && len(result) > len(current)) || (nowN > indexEntries && nowN > wasN)) {
		return ""
	}
	why := fmt.Sprintf("Your memory index would be %d/%d bytes and %d/%d entries, over its budget, so this write is refused. "+
		"Merge or delete entries first; an edit that shrinks the index is allowed.", len(result), indexBytes, nowN, indexEntries)
	if stale := staleMemory(home, agent, time.Now()); len(stale) > 0 {
		why += fmt.Sprintf(" Not used for %d+ days: %s.", staleDays, strings.Join(stale[:min(len(stale), staleListed)], ", "))
	}
	return why
}

// universalNewlines is what Python's read_text makes of line ends: \r\n and \r read as \n.
func universalNewlines(s string) string {
	return strings.ReplaceAll(strings.ReplaceAll(s, "\r\n", "\n"), "\r", "\n")
}

func entries(text string) int {
	n := 0
	for _, ln := range textsafe.SplitLines(text) {
		if strings.HasPrefix(ln, "- [") {
			n++
		}
	}
	return n
}

// wouldBe is the index as this call would leave it; ok false when that cannot be worked out (the call is malformed, or
// its edit does not apply - the tool fails it itself).
func wouldBe(current, tool string, input map[string]any) (string, bool) {
	if tool == "Write" {
		content, ok := input["content"].(string)
		return content, ok
	}
	var edits any = []any{input}
	if tool == "MultiEdit" {
		edits = input["edits"]
	}
	text := current
	list, _ := edits.([]any)
	for _, e := range list {
		m, _ := e.(map[string]any)
		old, ok1 := m["old_string"].(string)
		repl, ok2 := m["new_string"].(string)
		if !ok1 || !ok2 || !strings.Contains(text, old) {
			return "", false
		}
		n := 1
		if truthy(m["replace_all"]) {
			n = -1
		}
		text = strings.Replace(text, old, repl, n)
	}
	return text, truthy(edits)
}

// truthy is Python's bool() of a JSON value.
func truthy(v any) bool {
	switch x := v.(type) {
	case nil:
		return false
	case bool:
		return x
	case float64:
		return x != 0
	case string:
		return x != ""
	case []any:
		return len(x) > 0
	case map[string]any:
		return len(x) > 0
	}
	return true
}

// staleMemory is the agent's memory files, the index aside, last used (or, never used, last written) more than staleDays
// ago, in the order Python's sorted(rglob("*.md")) gives them (memory.status's `stale`).
func staleMemory(home, agent string, now time.Time) []string {
	dir := memoryDir(home, agent)
	use := map[string]any{}
	if b, err := os.ReadFile(filepath.Join(paths.AgentDir(home, agent), memoryUseFile)); err == nil {
		_ = json.Unmarshal(b, &use)
	}
	var rels []string
	_ = filepath.WalkDir(dir, func(p string, d fs.DirEntry, err error) error {
		if err == nil && p != dir && strings.HasSuffix(d.Name(), ".md") {
			rels = append(rels, filepath.ToSlash(p[len(dir)+1:]))
		}
		return nil
	})
	slices.SortFunc(rels, func(a, b string) int { return slices.Compare(strings.Split(a, "/"), strings.Split(b, "/")) })
	var stale []string
	for _, rel := range rels {
		if rel == memoryIndex {
			continue
		}
		last, recorded, ok := lastUse(use[rel])
		if !ok {
			continue // a value Python's float() refuses
		}
		if !recorded {
			st, err := os.Stat(filepath.Join(dir, rel))
			if err != nil {
				continue
			}
			last = float64(st.ModTime().UnixNano()) / 1e9
		}
		if float64(now.UnixNano())/1e9-last > staleDays*86400 {
			stale = append(stale, rel)
		}
	}
	return stale
}

// lastUse is float(use.get(rel) or mtime) for a recorded value: recorded false when the value is falsy and the mtime
// stands in, ok false when float() would refuse it.
func lastUse(v any) (last float64, recorded, ok bool) {
	if !truthy(v) {
		return 0, false, true
	}
	switch x := v.(type) {
	case bool:
		return 1, true, true
	case float64:
		return x, true, true
	case string:
		if f, err := strconv.ParseFloat(strings.TrimSpace(x), 64); err == nil {
			return f, true, true
		}
	}
	return 0, true, false
}
