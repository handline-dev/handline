package prompts

import (
	"errors"
	"fmt"
	"sort"
	"strconv"
	"strings"

	"github.com/handline/handline/go/internal/lock"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/structured"
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
			return Choice{Yes: c == "yes"}, nil
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
	if e.Structured != nil && e.Structured.Has(name) {
		return e.answerStructured(c, name, prompt, choice)
	}
	if lock.TerminalBusy(e.Home, name) { // refuse at once, before anything is done
		return nil, rpc.Refused(name+": "+typingElsewhere, "terminal_lock", "")
	}
	block, err := e.OpenBlockOf(name)
	if err != nil {
		return nil, err
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
	ok, said := e.Answer(block.ID, pick)
	if !ok {
		return nil, rpc.Refused(said, "", "")
	}
	return map[string]any{"accepted": true, "said": said}, nil
}

// Structured is the daemon's structured sessions: an agent that has one is answered by callback, not by keys on a screen.
type Structured interface {
	Has(agent string) bool
	Answer(agent, prompt string, allow bool, message, by string) error
}

// answerStructured answers a permission prompt of a structured agent. There is no terminal, so no terminal lock and no input holder
// apply; the prompt is named by its id as always, and a different open prompt is stale.
func (e *Env) answerStructured(c *rpc.Conn, name, prompt string, choice any) (any, error) {
	key, _ := choice.(string)
	if key != "allow" && key != "deny" {
		return nil, bad("this prompt offers allow, deny", "choice")
	}
	me, _ := c.Caller.ActorInfo()["name"].(string)
	err := e.Structured.Answer(name, prompt, key == "allow", "", me)
	if errors.Is(err, structured.ErrStale) || errors.Is(err, structured.ErrNoPrompt) {
		current, qerr := e.Reads.OpenPrompt(name)
		if qerr != nil {
			return nil, qerr
		}
		return nil, StaleError(current)
	}
	if err != nil {
		return nil, err
	}
	said := name + ": answered " + map[string]string{"allow": "Allow", "deny": "Deny"}[key]
	return map[string]any{"accepted": true, "said": said}, nil
}
