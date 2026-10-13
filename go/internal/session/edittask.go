package session

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/protected"
)

var editTools = map[string]bool{"Write": true, "Edit": true, "MultiEdit": true, "NotebookEdit": true}

// EditGuard is the edit gate (session/edittask.py): the reason to refuse an Edit, Write, MultiEdit or NotebookEdit while
// the agent holds no current task, "" to let it through. With no task it records an edit_gate event; only the founder's
// `off` in the protected store lets the call through then.
func EditGuard(d *sql.DB, home, agent, tool string, toolInput any) (string, error) {
	if agent == protected.Human || !editTools[tool] || editExempt(home, agent, toolInput) {
		return "", nil
	}
	if _, held, err := db.CurrentTask(d, agent); err != nil || held {
		return "", err
	}
	on := !protected.New(home).IsOff("gates", "edit_task")
	if _, err := db.AddEvent(d, agent, "edit_gate", db.KV{K: "tool", V: tool}, db.KV{K: "enforced", V: on}); err != nil || !on {
		return "", err
	}
	open, err := db.OpenTasks(d)
	if err != nil {
		return "", err
	}
	var mine []*db.Task
	for _, t := range open {
		if t.Owner == agent && t.DoneEvidence.String == "" {
			mine = append(mine, t)
		}
	}
	head := fmt.Sprintf("%s refused: %s holds no current task, and every change must belong to one (T295).", tool, agent) // local-ref-ok: Python's words
	if len(mine) == 0 {
		return head + " You own no open task. If the founder just told you to do this, run `hl task begin \"<title>\"` to open " +
			"one; otherwise ask whoever gave you the work: `hl say <them> \"please open a task for <this work>\"`.", nil
	}
	more := ""
	if len(mine) > 1 {
		var ids []string
		for _, t := range mine[1:min(len(mine), 4)] {
			ids = append(ids, fmt.Sprintf("T%d", t.ID))
		}
		more = fmt.Sprintf(" (or another of yours: %s)", strings.Join(ids, ", "))
	}
	title := []rune(mine[0].Title)
	return fmt.Sprintf("%s Run `hl task start T%d` to make your task \"%s\" current%s, then retry.",
		head, mine[0].ID, string(title[:min(len(title), 60)]), more), nil
}

// editExempt is a call on a working file, never the deliverable: the agent's memory, $TMPDIR, the plan-mode plan folder
// ($CLAUDE_CONFIG_DIR/plans) and its own handoff note - that one file, not through a link.
func editExempt(home, agent string, toolInput any) bool {
	input, _ := toolInput.(map[string]any)
	p := input["file_path"]
	if !truthy(p) {
		p = input["notebook_path"]
	}
	path, _ := p.(string)
	if path == "" {
		return false
	}
	real, note := paths.Realpath(path), paths.HandoffNoteFile(home, agent)
	if real == paths.Realpath(note) && !isLink(path) && !isLink(note) {
		return true
	}
	roots := []string{memoryDir(home, agent), os.Getenv("TMPDIR")}
	if cfg := os.Getenv("CLAUDE_CONFIG_DIR"); cfg != "" {
		roots = append(roots, filepath.Join(cfg, "plans"))
	}
	for _, root := range roots {
		if root == "" {
			continue
		}
		root = paths.Realpath(root)
		if real == root || strings.HasPrefix(real, root+"/") {
			return true
		}
	}
	return false
}

func isLink(p string) bool {
	st, err := os.Lstat(p)
	return err == nil && st.Mode()&os.ModeSymlink != 0
}

// PreToolUse is the reason the PreToolUse hook refuses a tool call, "" to let it through (events.record_from_stdin): an hl
// command that would substitute in double quotes, then the edit gate. A guard that fails lets
// the call through, as a hook must never break the session over itself.
func PreToolUse(d *sql.DB, home, agent, tool string, toolInput any) string {
	if why := Guard(tool, toolInput); why != "" {
		return why
	}
	why, _ := EditGuard(d, home, agent, tool, toolInput)
	return why
}
