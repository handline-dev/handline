// Package status is the one state and reason every agent and task is served with (the contract's x-status): what it is doing and why, in
// words a client shows as they are.
package status

import "time"

// The states.
const (
	Preparing = "preparing"
	Starting  = "starting"
	Running   = "running"
	Waiting   = "waiting"
	Stopped   = "stopped"
	Idle      = "idle"
	Off       = "off"
	Done      = "done"
)

// The reasons. Done is also the reason of a task its owner finished.
const (
	Work       = "work"
	Restart    = "restart"
	Retry      = "retry"
	Launch     = "launch"
	Turn       = "turn"
	Watch      = "watch"
	Handoff    = "handoff"
	Background = "background"
	You        = "you"
	Opener     = "opener"
	Task       = "task"
	Owner      = "owner"
	Clock      = "clock"
	Move       = "move"
	Load       = "load"
	Pace       = "pace"
	Wall       = "wall"
	Cap        = "cap"
	Stalled    = "stalled"
	None       = "none"
	Discarded  = "discarded"
)

// States and Reasons are every word, for the test that holds them to the contract.
var (
	States  = []string{Preparing, Starting, Running, Waiting, Stopped, Idle, Off, Done}
	Reasons = []string{Work, Restart, Retry, Launch, Turn, Watch, Handoff, Background, You, Opener, Task, Owner, Clock, Move, Load, Pace,
		Wall, Cap, Stalled, None, Done, Discarded}
)

// Status is one agent's or task's. Until and Since are unix seconds, 0 for none.
type Status struct {
	State, Reason, Text string
	Until, Since        int64
	Tasks               []int64
}

// Map is the wire's Status.
func (s Status) Map() map[string]any {
	tasks := s.Tasks
	if tasks == nil {
		tasks = []int64{}
	}
	return map[string]any{"state": s.State, "reason": s.Reason, "text": s.Text, "until": stamp(s.Until), "since": stamp(s.Since), "tasks": tasks}
}

func stamp(epoch int64) any {
	if epoch == 0 {
		return nil
	}
	return time.Unix(epoch, 0).UTC().Format("2006-01-02T15:04:05Z")
}
