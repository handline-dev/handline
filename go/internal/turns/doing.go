package turns

import (
	"encoding/json"
	"strings"
	"time"

	transcripts "github.com/handline/handline/go/internal/transcript"
)

// Doing is the running turn's newest tool call, as the contract's AgentView.doing has it: the tool, a space and the first line of what
// it was asked, cut to doingCap code points; At is when the call began, unix seconds.
type Doing struct {
	Text string
	At   int64
}

const doingCap = 60

// doingEvery bounds how often a new call alone changes what Doing says, so a turn that calls tools fast pushes its view at most this often.
const doingEvery = 5 * time.Second

// toolCall is the last tool call of an assistant entry, nil for an entry with none.
func toolCall(line []byte) *Doing {
	var e struct {
		Type      string `json:"type"`
		Timestamp string `json:"timestamp"`
		Message   struct {
			Content json.RawMessage `json:"content"`
		} `json:"message"`
	}
	if json.Unmarshal(line, &e) != nil || e.Type != "assistant" {
		return nil
	}
	var parts []struct {
		Type  string         `json:"type"`
		Name  string         `json:"name"`
		Input map[string]any `json:"input"`
	}
	json.Unmarshal(e.Message.Content, &parts)
	for i := len(parts) - 1; i >= 0; i-- {
		if p := parts[i]; p.Type == "tool_use" && p.Name != "" {
			text := p.Name
			if first, _, _ := strings.Cut(transcripts.ToolText(p.Input), "\n"); first != "" {
				text += " " + first
			}
			if r := []rune(text); len(r) > doingCap {
				text = string(r[:doingCap])
			}
			at := time.Now().Unix()
			if ts, err := time.Parse(time.RFC3339Nano, e.Timestamp); err == nil {
				at = ts.Unix()
			}
			return &Doing{Text: text, At: at}
		}
	}
	return nil
}

// Doing is the agent's running turn's newest tool call as last released, nil when no turn runs or it has called no tool yet.
func (t *Tracker) Doing(agent string) *Doing {
	t.mu.Lock()
	defer t.mu.Unlock()
	if tr := t.agents[agent]; tr != nil && tr.state == Working {
		return tr.doing
	}
	return nil
}

// called takes a tool call seen in the running turn: it shows at once when the last one shown is doingEvery old, else when it is.
func (t *Tracker) called(agent string, d *Doing) {
	t.mu.Lock()
	tr := t.get(agent)
	if tr.state != Working {
		t.mu.Unlock()
		return
	}
	every := doingEvery
	if t.doingEvery != 0 {
		every = t.doingEvery
	}
	tr.nextDoing = d
	if tr.release != nil {
		t.mu.Unlock()
		return
	}
	wait := every - time.Since(tr.shownAt)
	if wait > 0 {
		gen := tr.gen
		tr.release = time.AfterFunc(wait, func() { t.show(agent, tr, gen) })
		t.mu.Unlock()
		return
	}
	gen := tr.gen
	t.mu.Unlock()
	t.show(agent, tr, gen)
}

// show releases the newest call seen, unless the turn it was seen in has ended (a new turn starts with none).
func (t *Tracker) show(agent string, tr *turn, gen int) {
	t.mu.Lock()
	if tr.gen != gen || tr.nextDoing == nil {
		t.mu.Unlock()
		return
	}
	tr.release = nil
	tr.doing, tr.nextDoing, tr.shownAt = tr.nextDoing, nil, time.Now()
	changed := t.changed
	t.mu.Unlock()
	if changed != nil {
		changed(agent)
	}
}

// clearDoing forgets the calls of a turn that ended or was replaced.
func (tr *turn) clearDoing() {
	tr.doing, tr.nextDoing = nil, nil
	tr.gen++
	if tr.release != nil {
		tr.release.Stop()
		tr.release = nil
	}
}
