package session

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
	"unicode"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/term"
)

// An agent stopped at a permission prompt only the founder can answer. Such an agent emits no Stop, so its state stays RUNNING and
// nothing else says a person is wanted. The hook that sees the prompt writes one small file per agent; the hooks that see it
// answered take it away. A file, not a row: the clearing hook is PostToolUse, which fires on every tool call, so what it does when
// nothing is waiting has to be one stat and no database. That only clears the one tool call it names, so WaitOf also asks the
// database, once, at display time, for the case that leaves: a prompt answered without ever firing its own PostToolUse.

// ShownChars is how much of a tool's input a wait shows.
const ShownChars = 120

var inputFields = []string{"command", "file_path", "path", "pattern", "url", "query", "description"}

// MovedOnKinds is what proves the agent's own turn continued past the prompt that raised a wait, not merely that some other tool
// call finished. Deliberately narrower than the agent-activity kinds: SubagentStop looks the same shape as PostToolUse, and one of
// several parallel subagents finishing fires it while a sibling is still stuck at a prompt; TeammateIdle is another agent's status
// reaching this session. An `hl` verb only runs once the tool call that ran it has itself returned, and PostCompact is this
// session's own transcript growing; neither is a side effect an unrelated tool call could produce.
var MovedOnKinds = []string{"ask", "cancel", "commit", "raise_decision", "report", "resolve", "withdraw", "no_task", "PostCompact"}

// Wait is the permission prompt an agent is stopped at.
type Wait struct {
	Tool, Command, Reason string
	Since                 int64
	AfterEventID          *int64
	QuestionData          map[string]any
}

func (e *Env) waitFile(agent string) string { return filepath.Join(e.Home, "waiting", agent+".json") }

func printable(s string, n int) string {
	var b []rune
	for _, c := range s {
		if unicode.IsPrint(c) {
			b = append(b, c)
		} else {
			b = append(b, ' ')
		}
	}
	if len(b) > n {
		b = b[:n]
	}
	return string(b)
}

// Summary is the one line that says what the tool was asked to do, control characters flattened so it cannot draw over the screen
// it is shown on.
func Summary(toolInput any) string {
	var text string
	switch in := toolInput.(type) {
	case map[string]any:
		found := false
		for _, k := range inputFields {
			if v, ok := in[k].(string); ok && strings.TrimSpace(v) != "" {
				text, found = v, true
				break
			}
		}
		if !found {
			text = rpc.PyJSON(in, true, true)
		}
	case nil:
	case string:
		text = in
	case bool:
		text = map[bool]string{true: "True", false: "False"}[in]
	default:
		text = fmt.Sprint(in)
	}
	return printable(text, ShownChars)
}

// WaitReason is the tool call's own stated reason (a Bash call's description) beside the command it explains, or "" when the call
// carries none or is only that text.
func WaitReason(toolInput any) string {
	in, _ := toolInput.(map[string]any)
	command, ok := in["command"].(string)
	if !ok || strings.TrimSpace(command) == "" {
		return ""
	}
	if v, ok := in["description"].(string); ok {
		return printable(v, ShownChars)
	}
	return ""
}

func atomicWrite(path, text string) error {
	var salt [4]byte
	_, _ = rand.Read(salt[:])
	tmp := fmt.Sprintf("%s.%d.%s.tmp", path, os.Getpid(), hex.EncodeToString(salt[:]))
	if err := os.WriteFile(tmp, []byte(text), 0o666); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return nil
}

// NoteWait records that the agent is at a permission prompt for this tool call. afterEventID is that PermissionRequest's own row
// id in events, what WaitOf later asks whether anything has happened after, since a second-resolution clock ties too often to
// order two calls a real agent can make moments apart.
func (e *Env) NoteWait(agent, tool string, toolInput any, afterEventID *int64) error {
	f := e.waitFile(agent)
	if err := os.MkdirAll(filepath.Dir(f), 0o777); err != nil {
		return err
	}
	data := map[string]any{"tool": tool, "command": Summary(toolInput), "reason": WaitReason(toolInput), "since": e.now(), "after_event_id": nil}
	if afterEventID != nil {
		data["after_event_id"] = *afterEventID
	}
	// The founder's screen needs the whole AskUserQuestion call to render its questions and options, not the one-line summary
	// other readers get.
	if in, ok := toolInput.(map[string]any); ok && tool == "AskUserQuestion" {
		data["question_data"] = in
	}
	return atomicWrite(f, rpc.PyJSON(data, false, true))
}

// ReadWait is the agent's wait file, nil when there is none.
func (e *Env) ReadWait(agent string) *Wait {
	b, err := os.ReadFile(e.waitFile(agent))
	if err != nil {
		return nil
	}
	var m map[string]any
	if json.Unmarshal(b, &m) != nil || m == nil {
		return nil
	}
	since, _ := m["since"].(float64)
	if since == 0 {
		return nil
	}
	w := &Wait{Since: int64(since)}
	w.Tool, _ = m["tool"].(string)
	w.Command, _ = m["command"].(string)
	w.Reason, _ = m["reason"].(string)
	if id, ok := m["after_event_id"].(float64); ok {
		v := int64(id)
		w.AfterEventID = &v
	}
	w.QuestionData, _ = m["question_data"].(map[string]any)
	return w
}

// ClearWait is the prompt answered.
func (e *Env) ClearWait(agent string) {
	_ = os.Remove(e.waitFile(agent))
}

// ClearWaitFor clears only the prompt of this tool call: another tool finishing while this one still waits is not an answer.
func (e *Env) ClearWaitFor(agent, tool string, toolInput any) {
	if _, err := os.Stat(e.waitFile(agent)); err != nil {
		return
	}
	if held := e.ReadWait(agent); held != nil && (held.Tool != tool || held.Command != Summary(toolInput)) {
		return
	}
	e.ClearWait(agent)
}

// modalStillThere is whether the terminal's own screen still shows a dialog: the authoritative check, since events can be entirely
// silent for as long as an agent is mid-turn. Fail-closed, the same direction ScreenBlocker itself fails in: no terminal, or a
// screen that cannot be read right now, reads as the dialog is still there. Hiding a wait that is real is worse than showing one
// that is not. Backgrounded (the session picker showing instead) and nil (a plain composer) both mean no dialog is on screen.
// since is passed to the cache as notBefore: a read taken before this wait was even noted cannot prove it is gone.
func (e *Env) modalStillThere(handle string, since int64) bool {
	if handle == "" {
		return true
	}
	b := e.Term.ScreenBlockerCached(handle, time.Unix(since, 0))
	return b != nil && (b.Kind == term.Modal || b.Kind == term.Unreadable)
}

// WaitOf is the wait to show for an agent row, or nil. Only a session that is still running is waiting: a closed one, or one the
// daemon has since moved out of RUNNING, is not at that prompt any more whatever the file says.
//
// ClearWaitFor only removes the file for the exact tool call that raised it, which is right when a denial or an interruption means
// that call never fires its own PostToolUse, so nothing else ever clears it. So this asks a question no sibling tool call can
// answer: has the agent recorded activity of one of MovedOnKinds after the prompt that raised this wait? Finding one proves the
// turn that hit this prompt has already gone on, so the wait is stale and is cleared here. The comparison is by events.id, not
// since. A wait noted before this field existed has no id to compare and is left showing rather than guessed at.
//
// A third path: the agent's own terminal, read only for an agent that already has a wait file, through the short cache, since this
// runs on every redraw of the dashboard for as long as a wait stays open. Unlike the other two, a clean screen does not delete the
// file: it only hides this call's answer, because deleting on a cached "no dialog" could delete a second prompt's wait raised
// after that read was taken. A wait the screen hides here is shown again the moment a fresh read finds the dialog still up.
func (e *Env) WaitOf(row *AgentRow) *Wait {
	if row == nil || row.ClosedAt != 0 || row.State != Running {
		return nil
	}
	w := e.ReadWait(row.Agent)
	if w == nil {
		return nil
	}
	if w.AfterEventID != nil {
		if moved, ok := e.LastActivityID(row.Agent, MovedOnKinds); ok && moved > *w.AfterEventID {
			e.ClearWait(row.Agent)
			return nil
		}
	}
	if !e.modalStillThere(row.Terminal, w.Since) {
		return nil
	}
	return w
}
