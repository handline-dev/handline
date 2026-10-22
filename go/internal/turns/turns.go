// Package turns says whether a terminal agent is mid-turn from its session's hooks and transcript instead of its screen
// (docs/screen-reading-inventory.md §3 and §4). UserPromptSubmit opens a turn; Stop or StopFailure closes it. Esc fires no hook
// (docs/hook-probes.md §3), so while a turn is open its transcript is watched for the entry Claude Code writes on an interrupt.
//
// Where no event has said, the turn is Unknown and the caller reads the screen as before: before a session's first prompt (a launch
// or trust accept settling), after a daemon restart until the next hook, and background work after an Esc until the next Stop.
//
// A CLI killed hard runs no SessionEnd, so the session's process is watched too: its exit ends the turn and is told at once.
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

	mu         sync.Mutex
	agents     map[string]*turn
	changed    func(agent string)
	pid        func(agent string) int
	ended      func(agent string)
	reapWait   time.Duration // reapWait when 0
	doingEvery time.Duration // doingEvery when 0
}

// OnChange sets what is told, after the fact, each time an agent's turn opens or closes or a session starts: the agent's view is
// written then, not at the next tick.
func (t *Tracker) OnChange(f func(agent string)) {
	t.mu.Lock()
	t.changed = f
	t.mu.Unlock()
}

// Launched is told when a launch has begun at the agent's terminal: a session ended before it no longer says none runs there, and the
// old process's exit no longer ends it. Its view is told, as Refresh tells it.
func (t *Tracker) Launched(agent string) {
	t.mu.Lock()
	if tr := t.agents[agent]; tr != nil {
		tr.ended = false
		tr.stopPid()
	}
	t.mu.Unlock()
	t.Refresh(agent)
}

// Refresh tells OnChange's func of a change to the agent's view that is not its turn's.
func (t *Tracker) Refresh(agent string) {
	t.mu.Lock()
	changed := t.changed
	t.mu.Unlock()
	if changed != nil {
		changed(agent)
	}
}

// OnEnd sets how an agent's session process is found (0 when it is not) and what is told once that process has exited.
func (t *Tracker) OnEnd(pid func(agent string) int, ended func(agent string)) {
	t.mu.Lock()
	t.pid, t.ended = pid, ended
	t.mu.Unlock()
}

type turn struct {
	state      State
	at         int64 // when the hook that set state ran
	background []string
	bgKnown    bool          // background is what the last Stop said; an interrupt leaves it unknown
	settled    bool          // a Stop closed the turn and no prompt has been raised since
	ended      bool          // SessionEnd or the process's exit ended the session, and no launch or SessionStart came since
	changed    chan struct{} // closed on every change
	unwatch    func()
	pid        int // the session process watched for its exit
	unwatchPid func()
	// doing is the running turn's newest tool call shown, nextDoing one seen and not shown yet (release shows it); gen counts the turns
	// whose calls were forgotten, so a release of an earlier turn shows nothing.
	doing, nextDoing *Doing
	shownAt          time.Time
	release          *time.Timer
	gen              int
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
	tr.state, tr.settled = s, false
	tr.clearDoing()
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
	if event == "SessionStart" {
		tr.stopPid()
	}
	lookup := t.pid != nil && tr.pid == 0
	t.mu.Unlock()
	if (now != was || event == "SessionStart") && changed != nil { // a session's first hook also ends its launch's starting
		changed(agent)
	}
	if lookup {
		t.watchPid(agent)
	}
}

func (tr *turn) stopPid() {
	if tr.unwatchPid != nil {
		tr.unwatchPid()
		tr.unwatchPid = nil
	}
	tr.pid = 0
}

// watchPid starts watching the agent's session process, found outside the lock: finding it reads files.
func (t *Tracker) watchPid(agent string) {
	t.mu.Lock()
	find := t.pid
	t.mu.Unlock()
	pid := find(agent)
	if pid <= 0 || zombie(pid) { // the CLI a new session replaced, not yet reaped: a later hook finds the new one
		return
	}
	t.mu.Lock()
	defer t.mu.Unlock()
	tr := t.get(agent)
	if tr.pid != 0 {
		return
	}
	stop, err := watchExit(pid, func() { t.exited(agent, pid) })
	if err != nil {
		if t.Log != nil {
			t.Log("  turns: %s: cannot watch session pid %d, a hard kill is seen at the next tick: %v", agent, pid, err)
		}
		return
	}
	tr.pid, tr.unwatchPid = pid, stop
}

// reapWait bounds how long an exited session may stay a zombie before its end is told: the process table lists it until its shell reaps it.
const reapWait = 2 * time.Second

// exited ends the turn of a session whose process has gone, unless a newer session took the agent over meanwhile.
func (t *Tracker) exited(agent string, pid int) {
	wait := reapWait
	if t.reapWait != 0 {
		wait = t.reapWait
	}
	for deadline := time.Now().Add(wait); time.Now().Before(deadline) && !reaped(pid); {
		time.Sleep(20 * time.Millisecond)
	}
	t.mu.Lock()
	tr := t.get(agent)
	if tr.pid != pid {
		t.mu.Unlock()
		return
	}
	tr.unwatchPid, tr.pid = nil, 0
	tr.stopWatch()
	tr.ended = true
	was := tr.state
	tr.background, tr.bgKnown = nil, false
	tr.set(Unknown)
	changed, ended := t.changed, t.ended
	t.mu.Unlock()
	if was != Unknown && changed != nil {
		changed(agent)
	}
	if ended != nil {
		ended(agent)
	}
}

func (t *Tracker) hook(tr *turn, agent, event string, raw map[string]any, at int64) {
	if at < tr.at {
		return
	}
	tr.ended = event == "SessionEnd" // any other hook is a session speaking
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
		tr.settled = event == "Stop" // a StopFailure may leave the usage-limit dialog up
	case "PermissionRequest":
		tr.settled = false
	case "Notification":
		if raw["notification_type"] == "permission_prompt" {
			tr.settled = false
		}
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
		} else if d := toolCall(line); d != nil {
			t.called(agent, d)
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

// Settled is whether a Stop closed the turn at handle and no prompt has been raised since: no dialog of the session's waits on anyone.
func (t *Tracker) Settled(handle string) bool {
	agent := t.Agent(handle)
	if agent == "" {
		return false
	}
	t.mu.Lock()
	defer t.mu.Unlock()
	tr := t.agents[agent]
	return tr != nil && tr.settled
}

// Session is whether a session runs at handle: known while its process is watched (whose exit is told at once), or once SessionEnd or
// that exit said it ended; a process that lives on after SessionEnd (/clear) still runs. Otherwise known is false and the caller reads
// the screen.
func (t *Tracker) Session(handle string) (running, known bool) {
	agent := t.Agent(handle)
	if agent == "" {
		return false, false
	}
	t.mu.Lock()
	defer t.mu.Unlock()
	switch tr := t.agents[agent]; {
	case tr == nil:
		return false, false
	case tr.pid != 0:
		return true, true
	case tr.ended:
		return false, true
	}
	return false, false
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
