package prompts

import (
	"fmt"
	"github.com/handline/handline/go/internal/inputhold"
	"sort"
	"strconv"
	"strings"

	"github.com/handline/handline/go/internal/lock"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/term"
)

// prompt.answer: the answer to the permission prompt, question or dialog an agent's screen shows now, the one the caller saw
// (handline/daemon/rpc_prompts.py). prompt.list is the reads'. An answer names the prompt by its id, the screen block's
// fingerprint. When the open prompt is another, nothing is sent and the reply is stale_prompt with the current one. choice is one
// of the prompt's option keys, or {text} for the free-text answer to a question that has an Other row.

// typingElsewhere is the refusal's reason while whoever types at the agent holds the lock the tick takes.
const typingElsewhere = "someone else is typing at this agent"

// Register adds prompt.answer.
func Register(s *rpc.Server, e *Env) {
	s.Register("prompt.answer", rpc.Method{Role: "member", Write: true, Handler: e.answer})
	s.Register("permission.ask", rpc.Method{Role: "agent", Waits: true, Handler: e.permissionAsk})
}

func bad(msg, field string) error { return rpc.BadRequestError(msg, field, rpc.InvalidParams) }

func (e *Env) agentNamed(params map[string]any) (string, error) {
	name, _ := params["agent"].(string)
	if name == "" {
		return "", bad("agent is a name", "agent")
	}
	for _, n := range paths.ListAgents(e.Home) {
		if n == name {
			return name, nil
		}
	}
	err := rpc.NotFoundError("no such agent: " + name)
	err.Data = map[string]any{"what": "agent", "name": name}
	return "", err
}

// offered is the option keys prompt.list shows for a block: yes, no and Esc for a permission prompt that a hook record pins (the
// others are answered in the terminal), the numbered options of a question or a dialog, and Esc.
func offered(b *Block) map[string]bool {
	keys := map[string]bool{}
	switch b.Kind {
	case term.PromptPermission:
		if b.IsHook() {
			keys["allow"], keys["deny"] = true, true
			return keys // nothing on a screen to escape from
		}
		if !b.Answerable {
			return keys
		}
		keys["yes"], keys["no"] = true, true
	case term.PromptQuestion:
		opts, _ := b.Detail["options"].([]any)
		for i := range opts {
			keys[strconv.Itoa(i+1)] = true
		}
	default:
		opts, _ := b.Detail["options"].([]any)
		for _, o := range opts {
			if pair, _ := o.([]any); len(pair) >= 2 {
				keys[fmt.Sprint(pair[0])] = true
			}
		}
	}
	keys["esc"] = true
	return keys
}

func listed(keys map[string]bool) string {
	var out []string
	for k := range keys {
		out = append(out, k)
	}
	sort.Strings(out)
	if len(out) == 0 {
		return "no choice"
	}
	return strings.Join(out, ", ")
}

// answerFor is what Answer takes for choice on this block; bad_request for one the prompt does not offer.
func answerFor(b *Block, choice any) (Choice, error) {
	switch c := choice.(type) {
	case []any:
		if !truthy(b.Detail["multi_select"]) {
			return Choice{}, bad("only a multi prompt takes an array of keys", "choice")
		}
		keys := offered(b)
		delete(keys, "esc")
		seen := map[string]bool{}
		picks := []int{}
		for _, k := range c {
			s := k.(string)
			if !keys[s] || seen[s] {
				return Choice{}, bad(fmt.Sprintf("this prompt offers %s, each at most once", listed(keys)), "choice")
			}
			seen[s] = true
			n, _ := strconv.Atoi(s)
			picks = append(picks, n-1)
		}
		return Choice{Choices: picks}, nil
	case map[string]any:
		text, isStr := c["text"].(string)
		if len(c) != 1 || !isStr || text == "" {
			return Choice{}, bad("choice is an option key, or {text} with the text", "choice")
		}
		if b.Kind != term.PromptQuestion || !truthy(b.Detail["other_number"]) {
			return Choice{}, bad("this prompt takes no text answer", "choice")
		}
		return Choice{Choices: []int{}, Text: &text}, nil
	case string:
		keys := offered(b)
		if !keys[c] {
			return Choice{}, bad(fmt.Sprintf("this prompt offers %s, not %s", listed(keys), pyRepr(c)), "choice")
		}
		switch {
		case c == "esc":
			return Choice{Cancel: true}, nil
		case b.Kind == term.PromptPermission:
			return Choice{Yes: c == "yes" || c == "allow"}, nil
		case b.Kind == term.PromptQuestion:
			n, _ := strconv.Atoi(c)
			return Choice{Choices: []int{n - 1}}, nil
		}
		n, _ := strconv.Atoi(c)
		return Choice{Number: n}, nil
	}
	return Choice{}, bad("choice is an option key, or {text} with the text", "choice")
}

func pyRepr(s string) string {
	q := "'"
	if strings.Contains(s, "'") && !strings.Contains(s, `"`) {
		q = `"`
	}
	s = strings.NewReplacer(`\`, `\\`, "\n", `\n`, "\r", `\r`, "\t", `\t`).Replace(s)
	if q == "'" {
		s = strings.ReplaceAll(s, "'", `\'`)
	}
	return q + s + q
}

func (e *Env) answer(c *rpc.Conn, params map[string]any) (any, error) {
	prompt, _ := params["prompt"].(string)
	if prompt == "" {
		return nil, bad("prompt is the id of the prompt the caller saw", "prompt")
	}
	choice := params["choice"]
	switch ch := choice.(type) {
	case []any:
		for _, k := range ch {
			if _, ok := k.(string); !ok {
				return nil, bad("choice is an array of option keys", "choice")
			}
		}
	case string, map[string]any:
	default:
		return nil, bad("choice is an option key, or {text} with the text", "choice")
	}
	name, err := e.agentNamed(params)
	if err != nil {
		return nil, err
	}
	block, err := e.OpenBlockOf(name)
	if err != nil {
		return nil, err
	}
	if block == nil || !block.IsHook() { // a hook's question is answered through its call: no terminal is typed into
		if lock.TerminalBusy(e.Home, name) { // refuse at once, before anything is done
			return nil, rpc.Refused(name+": "+typingElsewhere, "terminal_lock", "")
		}
		if e.Holds != nil {
			row, err := e.Holds.Holder(name)
			if err != nil {
				return nil, err
			}
			if me, _ := c.Caller.Info["name"].(string); row != nil && row.Name != me {
				return nil, rpc.Refused(inputhold.HeldWhy(name, row.Name), "input_holder", "")
			}
		}
	}
	if block == nil || block.Fingerprint != prompt {
		current, err := e.Reads.OpenPrompt(name)
		if err != nil {
			return nil, err
		}
		return nil, StaleError(current)
	}
	pick, err := answerFor(block, choice)
	if err != nil {
		return nil, err
	}
	if note, has := params["note"]; has {
		text, ok := note.(string)
		if !ok || !block.IsHook() || pick.Yes {
			return nil, bad("note is a deny's reason, for a question from the hook", "note")
		}
		pick.Note = strings.TrimSpace(text)
	}
	ok, said := e.Answer(block.ID, pick)
	if !ok {
		return nil, rpc.Refused(said, "", "")
	}
	if err := e.recordAnswer(c, block, choice, pick); err != nil {
		return nil, err
	}
	if e.Holds != nil {
		me, _ := c.Caller.Info["name"].(string)
		if err := e.Holds.Touch(name, me); err != nil {
			return nil, err
		}
	}
	return map[string]any{"accepted": true, "said": said}, nil
}
