package prompts

import (
	"context"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/term"
)

// HookWaitS is how long a permission question from the PermissionRequest hook waits for the founder. With no answer by then the hook
// returns nothing and the agent's own dialog shows in its terminal.
const HookWaitS = 3600

// HookSource marks a prompt row raised by the hook: it is answered by returning the decision to the waiting call, not by typing.
const HookSource = "hook"

// Decision is the founder's answer to a hook's permission question.
type Decision struct {
	Allow bool
	Note  string // why not, for a deny
}

// hookWaits are the permission calls waiting now, by their prompt row.
type hookWaits struct {
	mu sync.Mutex
	by map[int64]chan Decision
}

func (w *hookWaits) add(id int64) chan Decision {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.by == nil {
		w.by = map[int64]chan Decision{}
	}
	ch := make(chan Decision, 1)
	w.by[id] = ch
	return ch
}

func (w *hookWaits) take(id int64) chan Decision {
	w.mu.Lock()
	defer w.mu.Unlock()
	ch := w.by[id]
	delete(w.by, id)
	return ch
}

// IsHook is whether the row was raised by the hook.
func (b *Block) IsHook() bool { return b.Detail["source"] == HookSource }

// AskPermission raises agent's permission question as a prompt row and waits for the founder's answer: answered false when ctx ends or
// HookWaitS passes first. A question already waiting for the same agent is waited out first, as a screen shows one at a time.
func (e *Env) AskPermission(ctx context.Context, agent, tool string, input map[string]any) (d Decision, answered bool, err error) {
	ctx, cancel := context.WithTimeout(ctx, HookWaitS*time.Second)
	defer cancel()
	command, reason := hookCommand(tool, input)
	title := agent + " wants to use " + tool
	panel := []string{title}
	if command != "" {
		panel = append(panel, command)
	}
	if reason != "" {
		panel = append(panel, reason)
	}
	detail := map[string]any{"agent": agent, "vendor": e.vendorOf(agent), "source": HookSource, "tool": tool, "command": command,
		"reason": reason, "title": title, "lines": panel}
	var id int64
	for id == 0 {
		now := e.now().Unix()
		// The time is in the fingerprint: the same command asked again is another question, and an answer to the last one is stale.
		res, err := e.DB.Exec("INSERT OR IGNORE INTO prompt_blocks(agent, kind, fingerprint, title, panel, detail, answerable, first_seen, last_seen) "+
			"VALUES(?,?,?,?,?,?,1,?,?)", agent, term.PromptPermission, Fingerprint(term.PromptPermission, append(panel, fmt.Sprint(now))), title,
			jsonText(panel), jsonText(detail), now, now)
		if err != nil {
			return d, false, err
		}
		if n, _ := res.RowsAffected(); n == 1 {
			id, _ = res.LastInsertId()
			break
		}
		select { // another prompt of this agent's is open: wait for it to go
		case <-ctx.Done():
			return d, false, nil
		case <-time.After(time.Second):
		}
	}
	ch := e.hooks.add(id)
	defer func() {
		e.hooks.take(id)
		e.closeRow(id, e.now().Unix())
	}()
	tick := time.NewTicker(StaleS / 3 * time.Second) // seen as fresh, as the screen watch keeps a screen's row
	defer tick.Stop()
	for {
		select {
		case d := <-ch:
			return d, true, nil
		case <-ctx.Done():
			return d, false, nil
		case <-tick.C:
			if _, err := e.DB.Exec("UPDATE prompt_blocks SET last_seen = ? WHERE id = ? AND gone_at IS NULL", e.now().Unix(), id); err != nil {
				return d, false, err
			}
		}
	}
}

// answerHook hands the founder's choice to the call waiting on row.
func (e *Env) answerHook(row *Block, c Choice) (bool, string) {
	ch := e.hooks.take(row.ID)
	if ch == nil {
		return e.record(row, false, row.Agent+": the question is no longer waiting - nothing sent")
	}
	ch <- Decision{Allow: c.Yes && !c.Cancel, Note: c.Note}
	if c.Yes && !c.Cancel {
		return e.record(row, true, row.Agent+": allowed")
	}
	return e.record(row, true, row.Agent+": denied")
}

// hookCommand is what the tool call does and why, from the hook's tool_input: a Bash command and its description, else the file or
// URL it works on.
func hookCommand(tool string, input map[string]any) (command, reason string) {
	reason = str(input, "description")
	for _, k := range []string{"command", "file_path", "notebook_path", "path", "url", "pattern"} {
		if v := strings.TrimSpace(str(input, k)); v != "" {
			return v, reason
		}
	}
	return "", reason
}

// permissionAsk is permission.ask: the caller's own session asks, and the call returns the founder's decision, or no decision once the
// wait is over.
func (e *Env) permissionAsk(c *rpc.Conn, params map[string]any) (any, error) {
	if c.Caller.Role != "agent" {
		return nil, rpc.Refused("only an agent's own session asks for a permission", "role", "")
	}
	agent := c.Caller.Actor()
	tool, _ := params["tool"].(string)
	if tool == "" {
		return nil, bad("tool is the tool's name", "tool")
	}
	input, ok := params["input"].(map[string]any)
	if !ok {
		return nil, bad("input is the tool call's input object", "input")
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() {
		select {
		case <-c.Done():
			cancel()
		case <-ctx.Done():
		}
	}()
	d, answered, err := e.AskPermission(ctx, agent, tool, input)
	if err != nil || !answered {
		return map[string]any{"behavior": nil}, err
	}
	if d.Allow {
		return map[string]any{"behavior": "allow"}, nil
	}
	msg := "The founder said no."
	if d.Note != "" {
		msg = "The founder said no: " + d.Note
	}
	return map[string]any{"behavior": "deny", "message": msg}, nil
}
