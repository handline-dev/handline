package structured

import (
	"context"
	"crypto/sha1"
	"encoding/hex"
	"errors"
	"fmt"
	"sync"
	"time"
)

// Verdict is what the policy step says about a tool call.
type Verdict int

const (
	Ask   Verdict = iota // no rule decides it: a person is asked
	Allow                // a rule allows it
	Deny                 // a guard refuses it
)

// Policy is the step before a person is asked: the guard that refuses a tool call, then the tools the agent may always use. The
// rule that matched is returned and recorded in the event.
type Policy struct {
	Guard func(tool string, input any) string // a reason to refuse, or ""; the shell guard
	Allow []string                            // tool names allowed without asking
}

func (p Policy) Decide(r ToolRequest) (v Verdict, rule, reason string) {
	if p.Guard != nil {
		if why := p.Guard(r.ToolName, r.Input); why != "" {
			return Deny, "guard", why
		}
	}
	for _, t := range p.Allow {
		if t == r.ToolName {
			return Allow, "allow:" + t, ""
		}
	}
	return Ask, "", ""
}

// PromptRow is a permission prompt as the prompt store shows it.
type PromptRow struct {
	Fingerprint string // the prompt's id for prompt.answer
	Title       string
	Lines       []string
	Detail      map[string]any
}

// PromptStore keeps the open prompt of an agent where prompt.list reads it. There is at most one open prompt per agent.
type PromptStore interface {
	Open(agent string, p PromptRow) (id int64, err error) // closes any prompt left open by a dead process
	Touch(id int64) error                                 // still waiting: prompt.list shows a prompt only while it is refreshed
	Close(id int64, answered, ok bool, said string) error
}

var (
	// ErrStale: the answer names a prompt that is not the open one.
	ErrStale = errors.New("not the open prompt")
	// ErrNoPrompt: the agent has no prompt waiting.
	ErrNoPrompt = errors.New("no prompt is waiting")
)

// Broker answers can_use_tool for every structured agent: policy first, else a prompt record and a wait for prompt.answer. No
// store, a store error, a timeout, a cancelled prompt or a closed session is a deny.
type Broker struct {
	Policy  func(agent string) Policy // per agent; nil allows nothing and guards nothing
	Store   PromptStore
	Events  EventSink
	Timeout time.Duration // how long a person has; zero is an hour
	Beat    time.Duration // how often a waiting prompt is refreshed; zero is 3 s

	mu      sync.Mutex
	waiting map[string]*pending // agent -> its open prompt
	turn    map[string]chan struct{}
}

type pending struct {
	fingerprint string
	toolUse     string
	answer      chan Decision
}

// Fingerprint is the id of one tool call's prompt.
func Fingerprint(agent, toolUse, tool string) string {
	sum := sha1.Sum([]byte("permission\n" + agent + "\n" + toolUse + "\n" + tool))
	return hex.EncodeToString(sum[:])
}

func (b *Broker) emit(agent, typ string, data map[string]any) {
	if b.Events != nil {
		b.Events.Emit(agent, Event{typ, data})
	}
}

func (b *Broker) lane(agent string) chan struct{} {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.turn == nil {
		b.turn = map[string]chan struct{}{}
	}
	if b.turn[agent] == nil {
		b.turn[agent] = make(chan struct{}, 1)
	}
	return b.turn[agent]
}

// CanUseTool is the Handlers.CanUseTool of agent's session.
func (b *Broker) CanUseTool(agent string) func(context.Context, ToolRequest) (Decision, error) {
	return func(ctx context.Context, r ToolRequest) (Decision, error) { return b.decide(ctx, agent, r) }
}

func (b *Broker) decide(ctx context.Context, agent string, r ToolRequest) (Decision, error) {
	var pol Policy
	if b.Policy != nil {
		pol = b.Policy(agent)
	}
	v, rule, why := pol.Decide(r)
	switch v {
	case Allow:
		b.emit(agent, "permission_answer", map[string]any{"tool_use": r.ToolUse, "tool": r.ToolName, "allow": true, "by": "policy", "rule": rule})
		return Decision{Allow: true}, nil
	case Deny:
		b.emit(agent, "permission_answer", map[string]any{"tool_use": r.ToolUse, "tool": r.ToolName, "allow": false, "by": "policy", "rule": rule, "said": why})
		return Decision{Message: why}, nil
	}
	if b.Store == nil {
		return Decision{}, errors.New("no prompt store")
	}
	// One open prompt per agent: a second call waits its turn, and gives up with the first if the session closes.
	lane := b.lane(agent)
	select {
	case lane <- struct{}{}:
		defer func() { <-lane }()
	case <-ctx.Done():
		return Decision{}, ctx.Err()
	}
	// The prompt's id is the tool call's id (contract: Transcript stream, Permission); the hash only stands in for a call that has none.
	fp := r.ToolUse
	if fp == "" {
		fp = Fingerprint(agent, r.ToolUse, r.ToolName)
	}
	row := PromptRow{Fingerprint: fp, Title: "Allow " + r.ToolName + "?", Lines: lines(r), Detail: map[string]any{
		"agent": agent, "vendor": "claude", "structured": true, "tool": r.ToolName, "input": r.Input, "reason": r.Reason, "command": command(r)}}
	id, err := b.Store.Open(agent, row)
	if err != nil {
		return Decision{}, fmt.Errorf("record the prompt: %w", err)
	}
	p := &pending{fingerprint: fp, toolUse: r.ToolUse, answer: make(chan Decision, 1)}
	b.mu.Lock()
	if b.waiting == nil {
		b.waiting = map[string]*pending{}
	}
	b.waiting[agent] = p
	b.mu.Unlock()
	b.emit(agent, "permission_request", map[string]any{"prompt": fp, "block": id, "tool_use": r.ToolUse, "tool": r.ToolName, "input": r.Input, "reason": r.Reason})

	timeout, beat := b.Timeout, b.Beat
	if timeout == 0 {
		timeout = time.Hour
	}
	if beat == 0 {
		beat = 3 * time.Second
	}
	deadline := time.NewTimer(timeout)
	tick := time.NewTicker(beat)
	defer deadline.Stop()
	defer tick.Stop()
	var d Decision
	answered := false
	by := "closed" // nobody answered: the time ran out
wait:
	for {
		select {
		case d = <-p.answer:
			answered = true
			break wait
		case <-tick.C:
			b.Store.Touch(id)
		case <-deadline.C:
			d = Decision{Message: "no one answered the permission prompt in time"}
			break wait
		case <-ctx.Done():
			d = Decision{Message: "the permission prompt was cancelled"}
			by = "cancel"
			break wait
		}
	}
	// Leave the table and take any answer sent meanwhile in one step with Answer, so a reported answer is the one that was used.
	b.mu.Lock()
	delete(b.waiting, agent)
	if !answered {
		select {
		case d = <-p.answer:
			answered = true
		default:
		}
	}
	b.mu.Unlock()
	b.Store.Close(id, answered, answered && d.Allow, d.Message)
	if !answered {
		b.emit(agent, "permission_answer", map[string]any{"prompt": fp, "tool_use": r.ToolUse, "tool": r.ToolName, "allow": false, "by": by, "said": d.Message})
		return d, ctx.Err()
	}
	return d, nil
}

// Has says whether agent has a prompt waiting.
func (b *Broker) Has(agent string) bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.waiting[agent] != nil
}

// Answer answers the prompt agent waits on. prompt is its fingerprint; a different one is ErrStale and nothing is sent. allow false
// denies, and message is what the model is told.
func (b *Broker) Answer(agent, prompt string, allow bool, message, by string) error {
	d := Decision{Allow: allow, Message: message}
	if !allow && d.Message == "" {
		d.Message = "the user denied the tool call"
	}
	b.mu.Lock()
	p := b.waiting[agent]
	switch {
	case p == nil:
		b.mu.Unlock()
		return ErrNoPrompt
	case p.fingerprint != prompt:
		b.mu.Unlock()
		return ErrStale
	}
	delete(b.waiting, agent) // the prompt leaves the table in the same step as its answer is sent: one answer per prompt
	// The event goes before the decision, so it comes before whatever the CLI does with it.
	b.emit(agent, "permission_answer", map[string]any{"prompt": prompt, "tool_use": p.toolUse, "allow": allow, "by": by, "said": d.Message})
	p.answer <- d // buffered, and taken once
	b.mu.Unlock()
	return nil
}

func command(r ToolRequest) string {
	s, _ := r.Input["command"].(string)
	return s
}

func lines(r ToolRequest) []string {
	if c := command(r); c != "" {
		return []string{c}
	}
	var out []string
	for k, v := range r.Input {
		out = append(out, fmt.Sprintf("%s: %v", k, v))
	}
	return out
}
