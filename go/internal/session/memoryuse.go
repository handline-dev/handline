package session

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"syscall"

	"github.com/handline/handline/go/internal/atomicfile"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
)

const (
	memoryIndex   = "MEMORY.md"
	memoryUseFile = "memory-use.json"
)

func memoryDir(home, agent string) string {
	return filepath.Join(paths.AgentDir(home, agent), "memory")
}

// memoryRel is the path a tool call names, relative to the agent's memory folder; ok false when it names something else
// (memoryuse._relative).
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

// renoteS is how recently a noted memory file is not noted again: a day's granularity is all the stale rule needs.
const renoteS = 24 * 3600

// NoteMemoryUse records that the agent read or wrote one of its memory files, the index aside, in
// agents/<name>/memory-use.json ({path relative to the folder: unix seconds}), for `hl memory status` (memoryuse.note_use).
// The read-modify-write holds a lock that waits, as two PostToolUse hooks of one turn can run at once.
func NoteMemoryUse(home, agent, tool string, toolInput any, now int64) error {
	input, ok := toolInput.(map[string]any)
	if !ok || (tool != "Read" && tool != "Write" && tool != "Edit" && tool != "MultiEdit") {
		return nil
	}
	rel, ok := memoryRel(home, agent, input)
	if !ok || rel == memoryIndex {
		return nil
	}
	f := filepath.Join(paths.AgentDir(home, agent), memoryUseFile)
	if lock, err := os.OpenFile(f+".lock", os.O_CREATE|os.O_RDWR, 0o600); err == nil {
		defer lock.Close()
		_ = syscall.Flock(int(lock.Fd()), syscall.LOCK_EX)
	}
	used := map[string]any{}
	if b, err := os.ReadFile(f); err == nil {
		d := json.NewDecoder(bytes.NewReader(b))
		d.UseNumber()
		var v any
		if d.Decode(&v) == nil {
			if m, ok := v.(map[string]any); ok {
				used = m
			}
		}
	}
	if v, ok := used[rel]; ok {
		then, ok := wholeSeconds(v)
		if !ok {
			return fmt.Errorf("%s: %v is not a time", rel, v) // Python's int() raises, and its hook writes nothing
		}
		if now-then < renoteS {
			return nil
		}
	}
	used[rel] = json.Number(strconv.FormatInt(now, 10))
	return atomicfile.Write(f, indented(used, "")+"\n")
}

// wholeSeconds is int(v or 0) for a value read with UseNumber: ok false where Python's int() would raise.
func wholeSeconds(v any) (int64, bool) {
	switch x := v.(type) {
	case nil:
		return 0, true
	case bool:
		return map[bool]int64{true: 1}[x], true
	case json.Number:
		if n, err := x.Int64(); err == nil {
			return n, true
		}
		if f, err := x.Float64(); err == nil {
			return int64(f), true
		}
	case string:
		if x == "" {
			return 0, true
		}
		if n, err := strconv.ParseInt(strings.TrimSpace(x), 10, 64); err == nil {
			return n, true
		}
	case []any:
		return 0, len(x) == 0
	case map[string]any:
		return 0, len(x) == 0
	}
	return 0, false
}

// indented is json.dumps(v, sort_keys=True, indent=1) at this indent.
func indented(v any, at string) string {
	in := at + " "
	switch x := v.(type) {
	case map[string]any:
		if len(x) == 0 {
			return "{}"
		}
		keys := make([]string, 0, len(x))
		for k := range x {
			keys = append(keys, k)
		}
		slices.Sort(keys)
		parts := make([]string, len(keys))
		for i, k := range keys {
			parts[i] = in + rpc.PyJSON(k, false, true) + ": " + indented(x[k], in)
		}
		return "{\n" + strings.Join(parts, ",\n") + "\n" + at + "}"
	case []any:
		if len(x) == 0 {
			return "[]"
		}
		parts := make([]string, len(x))
		for i, e := range x {
			parts[i] = in + indented(e, in)
		}
		return "[\n" + strings.Join(parts, ",\n") + "\n" + at + "]"
	}
	return rpc.PyJSON(v, true, true)
}
