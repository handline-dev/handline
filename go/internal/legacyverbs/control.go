package legacyverbs

import (
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/prompts"
	"github.com/handline/handline/go/internal/pyval"
	"github.com/handline/handline/go/internal/sessionrpc"
)

// controlsFor is the session controls and the agent a close or restart names, or why it cannot be served (refusal).
func (e *Env) controlsFor(p map[string]any) (c sessionrpc.Controls, agent, refusal string, err error) {
	if agent, err = text(p, "agent"); err != nil {
		return nil, "", "", err
	}
	if e.Controls != nil {
		c = e.Controls()
	}
	switch {
	case c == nil:
		return nil, agent, agent + ": sessions are not driven by this daemon yet", nil
	case !e.agentNamed(agent):
		return nil, agent, "no such agent: " + agent, nil
	}
	return c, agent, "", nil
}

// close gives an idle agent's memory back, keeping its terminal; fresh also drops the session id. Python's close verb never forced a
// working agent, so the call is not a person's.
func (e *Env) close(p map[string]any) (bool, string, error) {
	c, agent, refusal, err := e.controlsFor(p)
	if err != nil || refusal != "" {
		return false, refusal, err
	}
	return c.Close(sessionrpc.Call{By: db.Human}, agent, pyval.Truthy(p["fresh"]))
}

// restart ends the agent's session and starts it again; by is who asked, the founder when the request does not say.
func (e *Env) restart(p map[string]any) (bool, string, error) {
	c, agent, refusal, err := e.controlsFor(p)
	if err != nil || refusal != "" {
		return false, refusal, err
	}
	by := db.Human
	if pyval.Truthy(p["by"]) {
		by = pyval.Str(p["by"])
	}
	return c.Restart(sessionrpc.Call{By: by}, agent, pyval.Truthy(p["fresh"]), pyval.Truthy(p["force"]))
}

// agentSet is an agent's own change of its settings; the speaker is the caller's word, and only the agent itself may change them.
func (e *Env) agentSet(p map[string]any) (bool, string, error) {
	agent, err := text(p, "agent")
	if err != nil {
		return false, "", err
	}
	values := map[string]string{}
	if m, _ := p["values"].(map[string]any); m != nil {
		for k, v := range m {
			values[k] = pyval.Str(v)
		}
	}
	speaker := ""
	if s, ok := p["speaker"].(string); ok {
		speaker = s
	}
	ok, said := e.SetByAgent(agent, values, speaker)
	return ok, said, nil
}

// answerBlock answers a block of the prompts the daemon watches; the choice is what to press (permission {yes}; question {choices, text};
// dialog {number, always}; any kind {cancel}).
func (e *Env) answerBlock(p map[string]any) (bool, string, error) {
	block, err := number(p, "block")
	if err != nil {
		return false, "", err
	}
	var c prompts.Choice
	if m, _ := p["choice"].(map[string]any); m != nil {
		c.Cancel, c.Yes, c.Always = pyval.Truthy(m["cancel"]), pyval.Truthy(m["yes"]), pyval.Truthy(m["always"])
		if list, ok := m["choices"].([]any); ok {
			for _, v := range list {
				if n, ok := pyval.Int(v); ok {
					c.Choices = append(c.Choices, int(n))
				}
			}
		}
		if s, ok := m["text"].(string); ok {
			c.Text = &s
		}
		if n, ok := pyval.Int(m["number"]); ok {
			c.Number = int(n)
		}
	}
	ok, said := e.Prompts.Answer(block, c)
	return ok, said, nil
}
