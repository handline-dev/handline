// Package turns says whether a terminal agent is mid-turn from its session's hooks and transcript instead of its screen
// (docs/screen-reading-inventory.md §3 and §4). UserPromptSubmit opens a turn; Stop or StopFailure closes it. Esc fires no hook
// (docs/hook-probes.md §3), so while a turn is open its transcript is watched for the entry Claude Code writes on an interrupt.
//
// Where no event has said, the turn is Unknown and the caller reads the screen as before: before a session's first prompt (a launch
// or trust accept settling), after a daemon restart until the next hook, and background work after an Esc until the next Stop.
package turns

import (
	"fmt"
	"sync"
	"time"
)

type State int

const (
	Unknown State = iota
	Working
	Idle
)

// Tracker holds each agent's turn. Agent maps a terminal to its agent; Interrupted is told after an Esc closed a turn.
type Tracker struct {
	Agent       func(handle string) string
	Interrupted func(agent string)
	Log         func(format string, args ...any)

	mu      sync.Mutex
	agents  map[string]*turn
	changed func(agent string)
}

// OnChange sets what is told, after the fact, each time an agent's turn opens or closes or a session starts: the agent's view is
// written then, not at the next tick.
func (t *Tracker) OnChange(f func(agent string)) {
	t.mu.Lock()
	t.changed = f
	t.mu.Unlock()
}

type turn struct {
	state      State
	at         int64 // when the hook that set state ran
	background []string
	bgKnown    bool          // background is what the last Stop said; an interrupt leaves it unknown
	changed    chan struct{} // closed on every change
	unwatch    func()
}

func (t *Tracker) get(agent string) *turn {
	if t.agents == nil {
		t.agents = map[string]*turn{}
	}
	tr := t.agents[agent]
	if tr == nil {
		tr = &turn{changed: make(chan struct{})}
		t.agents[agent] = tr
	}
	return tr
}

func (tr *turn) set(s State) {
	tr.state = s
	close(tr.changed)
	tr.changed = make(chan struct{})
}

func (tr *turn) stopWatch() {
	if tr.unwatch != nil {
		tr.unwatch()
		tr.unwatch = nil
	}
}

// Hook takes one recorded hook event. One older than the event the turn was last set by moves nothing.
func (t *Tracker) Hook(agent, event string, raw map[string]any, at int64) {
	t.mu.Lock()
	tr := t.get(agent)
	was, changed := tr.state, t.changed
	t.hook(tr, agent, event, raw, at)
	now := tr.state
	t.mu.Unlock()
	if (now != was || event == "SessionStart") && changed != nil { // a session's first hook also ends its launch's starting
		changed(agent)
	}
}

func (t *Tracker) hook(tr *turn, agent, event string, raw map[string]any, at int64) {
	if at < tr.at {
		return
	}
	switch event {
	case "SessionStart", "SessionEnd":
		tr.stopWatch()
		tr.at, tr.background, tr.bgKnown = at, nil, false
		tr.set(Unknown)
	case "UserPromptSubmit":
		tr.at = at
		tr.set(Working)
		if path, _ := raw["transcript_path"].(string); path != "" && tr.unwatch == nil {
			tr.unwatch = t.watchTranscript(agent, path)
		}
	case "Stop", "StopFailure":
		tr.stopWatch()
		tr.at, tr.background, tr.bgKnown = at, runningTasks(raw["background_tasks"]), true
		tr.set(Idle)
	}
}

// runningTasks are the descriptions of the entries of Stop's background_tasks still running.
func runningTasks(v any) []string {
	list, _ := v.([]any)
	var out []string
	for _, e := range list {
		m, _ := e.(map[string]any)
		if m == nil || m["status"] != "running" {
			continue
		}
		d, _ := m["description"].(string)
		out = append(out, d)
	}
	return out
}

func (t *Tracker) watchTranscript(agent, path string) func() {
	stop, err := watchLines(path, func(line []byte) {
		if interruptEntry(line) {
			t.interrupt(agent)
		}
	})
	if err != nil {
		if t.Log != nil {
			t.Log("  turns: %s: cannot watch the transcript, an Esc will not be seen: %v", agent, err)
		}
		return nil
	}
	return stop
}

func (t *Tracker) interrupt(agent string) {
	t.mu.Lock()
	tr := t.get(agent)
	if tr.state != Working {
		t.mu.Unlock()
		return
	}
	tr.stopWatch()
	tr.background, tr.bgKnown = nil, false
	tr.set(Idle)
	changed := t.changed
	t.mu.Unlock()
	if t.Interrupted != nil {
		t.Interrupted(agent)
	}
	if changed != nil {
		changed(agent)
	}
}

// State is the agent's turn as its hooks last said.
func (t *Tracker) State(agent string) State {
	t.mu.Lock()
	defer t.mu.Unlock()
	if tr := t.agents[agent]; tr != nil {
		return tr.state
	}
	return Unknown
}

// WaitIdle waits up to timeout for the turn at handle to close. known is false when the hooks have not said, or stopped saying.
func (t *Tracker) WaitIdle(handle string, timeout time.Duration) (idle, known bool) {
	agent := t.Agent(handle)
	if agent == "" {
		return false, false
	}
	deadline := time.NewTimer(timeout)
	defer deadline.Stop()
	for {
		t.mu.Lock()
		tr := t.get(agent)
		state, changed := tr.state, tr.changed
		t.mu.Unlock()
		switch state {
		case Unknown:
			return false, false
		case Idle:
			return true, true
		}
		select {
		case <-changed:
		case <-deadline.C:
			return false, true
		}
	}
}

// Background is the work the last Stop said still runs, as the footer would count it ("" is none).
func (t *Tracker) Background(handle string) (running string, known bool) {
	agent := t.Agent(handle)
	if agent == "" {
		return "", false
	}
	t.mu.Lock()
	defer t.mu.Unlock()
	tr := t.get(agent)
	if tr.state != Idle || !tr.bgKnown {
		return "", false
	}
	switch n := len(tr.background); n {
	case 0:
		return "", true
	case 1:
		return "1 background task", true
	default:
		return fmt.Sprintf("%d background tasks", n), true
	}
}
