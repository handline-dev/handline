package lifecycle

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"sort"
	"strings"
	"syscall"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/session"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/term"
)

// The words a stall carries, which the sweep finds it by when the world refutes it.
const (
	TrustStall     = "folder-trust prompt"
	TrustDecision  = "[trust stall]"
	DialogStall    = "start-up dialog"
	DialogDecision = "[dialog stall]"
)

// TrustStallPrefixes are what the decisions a trust stall raised start with.
func TrustStallPrefixes(agent string) []string {
	return []string{TrustDecision, "[trust] " + agent + "'s terminal is showing"}
}

// ReconcilerPorts is everything the startup pass and the sweep reach that another group owns.
type ReconcilerPorts struct {
	Agents           func() []string                                                                // paths.list_agents
	Observe          func(agents []string, terminals map[string]string) map[string]session.Observed // session/observe.observe
	NoteSeen         func(seen map[string]session.Observed, at time.Time)                           // delivery's last look at the table
	ResumeHandoff    func(q db.Q, h db.HandoffRow) error                                            // handoff.resume_handoff
	EnforceEdges     func(q db.Q, agent string) error                                               // delivery.enforce_edges
	WaitingFor       func(q db.Q, agent string) (bool, error)                                       // lifecycle.waiting_for
	WakeAgent        func(q db.Q, agent string) (bool, string, error)                               // lifecycle.wake_agent
	QueuedOrIdle     func(q db.Q, agent, why string) (string, error)                                // evaluate.queued_or_idle: the state it chose
	RefreshView      func(q db.Q, agent string)                                                     // agentview.refresh_one
	LiftLogin        func(q db.Q, agent string, row *db.AgentState, actions *[]string)              // login_expired.lift
	StillDrawn       func(handle string) bool                                                       // delivery.session_still_drawn(wait=False)
	ResumableSession func(q db.Q, agent string) string                                              // the session id to remember for a closed agent
	Resolve          func(id int64, why string) error                                               // ask.resolve(done, no notice)
	Term             interface {
		Available() bool
		TrustPromptGone(handle string) bool
		ScreenBlocker(handle string) *term.Blocker
		IdleNow(handle string) bool
	}
	// Run is the structured sessions; nil keeps every agent a terminal agent.
	Run Runner
	Now func() int64
}

// Reconciler is daemon/reconciler.py: the startup pass and the per-tick sweep.
type Reconciler struct {
	Q     db.Q
	Log   func(string)
	Ports ReconcilerPorts

	probeTurn      int
	saidExhaustion int64
	saidDrawn      map[string]string
}

// ownFailure is an error that belongs to one agent - its unreadable agent.yaml or a file it needs - as against the daemon's own fault,
// which the pass keeps propagating: the class Python's reconcile catches (ConfigError, UnicodeDecodeError, OSError).
func ownFailure(err error) bool {
	var cfg *ConfigError
	var pathErr *fs.PathError
	var linkErr *os.LinkError
	var sysErr *os.SyscallError
	var errno syscall.Errno
	return errors.As(err, &cfg) || errors.As(err, &pathErr) || errors.As(err, &linkErr) || errors.As(err, &sysErr) || errors.As(err, &errno)
}

func isExhaustion(err error) bool {
	return errors.Is(err, syscall.EMFILE) || errors.Is(err, syscall.ENFILE)
}

// sayExhaustion says a descriptor shortage at most once a minute, so a spell of it does not become the log.
func (r *Reconciler) sayExhaustion(err error, doing string) {
	if now := r.now(); r.saidExhaustion == 0 || now-r.saidExhaustion >= exhaustionEveryS {
		r.saidExhaustion = now
		r.logf("out of file descriptors while %s: %v", doing, err)
	}
}

const exhaustionEveryS = 60

func (r *Reconciler) now() int64 {
	if r.Ports.Now != nil {
		return r.Ports.Now()
	}
	return time.Now().Unix()
}

func (r *Reconciler) logf(format string, a ...any) { r.Log(fmt.Sprintf(format, a...)) }

// ExpireHandoffs fails any handoff past its deadline. It runs every tick: checked only at startup, a handoff that wedged while the
// daemon stayed up was never timed out and its agent sat in HANDOFF, out of trigger evaluation, for ever.
func (r *Reconciler) ExpireHandoffs() ([]string, error) {
	var actions []string
	now := r.now()
	rows, err := db.InFlightHandoffs(r.Q)
	if err != nil {
		return nil, err
	}
	for _, h := range rows {
		if !h.Deadline.Valid || h.Deadline.Int64 == 0 || h.Deadline.Int64 >= now {
			continue
		}
		if err := db.AdvanceHandoff(r.Q, h.ID, h.Step, "degraded", fmt.Sprintf("deadline passed at %d", h.Deadline.Int64)); err != nil {
			return actions, err
		}
		if err := db.BumpCounter(r.Q, h.Agent, "throttle_count", 1); err != nil {
			return actions, err
		}
		if err := db.SetAgentState(r.Q, h.Agent, states.Degraded, now+states.DegradedRetryS, fmt.Sprintf("handoff timed out at step '%s'", h.Step)); err != nil {
			return actions, err
		}
		actions = append(actions, fmt.Sprintf("%s: handoff timed out at step '%s' -> DEGRADED", h.Agent, h.Step))
		r.logf("  %s: handoff timed out at step '%s' -> DEGRADED", h.Agent, h.Step)
	}
	return actions, nil
}

// stallAgent counts one agent's own failure against it and stalls it once the streak reaches MaxFailedAttempts, rather than let it
// wedge every other agent behind it. What counts as the agent's own is the caller's decision.
func (r *Reconciler) stallAgent(agent string, actions *[]string, cause error, where string) error {
	if isExhaustion(cause) {
		// The daemon ran out of descriptors: no agent's fault, and three of these stalled every agent in the team.
		r.sayExhaustion(cause, fmt.Sprintf("%s for %s", where, agent))
		return nil
	}
	if err := db.BumpCounter(r.Q, agent, "failed_attempts", 1); err != nil {
		return err
	}
	fresh, err := db.GetAgentState(r.Q, agent)
	if err != nil {
		return err
	}
	*actions = append(*actions, fmt.Sprintf("%s: %s: %v", agent, where, cause))
	r.logf("  %s: %s: %v", agent, where, cause)
	if fresh != nil && fresh.FailedAttempts >= states.MaxFailedAttempts {
		if err := db.SetAgentState(r.Q, agent, states.Stalled, 0, fmt.Sprintf("%d failed attempts: %s: %v", fresh.FailedAttempts, where, cause)); err != nil {
			return err
		}
		r.logf("  %s: STALLED after %d failed attempts", agent, fresh.FailedAttempts)
	}
	return nil
}

// StallAgent is stallAgent for a caller outside the package: the tick counts one agent's failing turn against it the same way.
func (r *Reconciler) StallAgent(agent string, actions *[]string, cause error, where string) error {
	return r.stallAgent(agent, actions, cause, where)
}

// Reconcile is the one unconditional pass at startup: crossings missed while the daemon was down, handoffs left in flight by a kill,
// and wake_at deadlines that elapsed with nobody listening.
func (r *Reconciler) Reconcile() ([]string, error) {
	now := r.now()
	actions, err := r.ExpireHandoffs()
	if err != nil {
		return actions, err
	}
	// Finish the handoffs still in flight from the step they reached: a log line claiming a resume is not a resume.
	rows, err := db.InFlightHandoffs(r.Q)
	if err != nil {
		return actions, err
	}
	for _, h := range rows {
		if err := r.Ports.ResumeHandoff(r.Q, h); err != nil {
			if !ownFailure(err) {
				return actions, err
			}
			// One agent's unreadable agent.yaml must not leave every other handoff, and every due wake below, unresolved.
			if e := r.stallAgent(h.Agent, &actions, err, fmt.Sprintf("could not resume handoff from step '%s'", h.Step)); e != nil {
				return actions, e
			}
			continue
		}
		actions = append(actions, fmt.Sprintf("%s: resumed in-flight handoff from step '%s'", h.Agent, h.Step))
	}

	due, err := db.DueAgents(r.Q, now)
	if err != nil {
		return actions, err
	}
	for _, row := range due {
		agent := row.Agent
		if row.State != states.Parked && row.State != states.Throttled && row.State != states.Degraded {
			continue
		}
		closed := row.ClosedAt.Valid && row.ClosedAt.Int64 != 0
		// A closed agent has no session left to notice its own wake_at, so it is woken here, before the deadline is cleared: a wake that
		// failed - the dead terminal a reboot leaves is the ordinary way - has to stay due, or the agent sleeps until a human looks.
		if closed {
			if err := r.Ports.EnforceEdges(r.Q, agent); err != nil {
				return actions, err
			}
		}
		waiting := false
		if closed && (row.Terminal.String != "" || r.structured(agent)) {
			if waiting, err = r.Ports.WaitingFor(r.Q, agent); err != nil {
				return actions, err
			}
		}
		if waiting {
			ok, why, err := r.Ports.WakeAgent(r.Q, agent)
			if err != nil {
				if !ownFailure(err) {
					return actions, err
				}
				if e := r.stallAgent(agent, &actions, err, "could not wake at reconciliation"); e != nil {
					return actions, e
				}
				continue
			}
			if !ok {
				actions = append(actions, fmt.Sprintf("%s: could not wake at reconciliation, still due - %s", agent, why))
				continue
			}
			actions = append(actions, agent+": woken at reconciliation")
		}
		next, err := r.Ports.QueuedOrIdle(r.Q, agent, "woke from "+row.State+" at reconciliation")
		if err != nil {
			return actions, err
		}
		actions = append(actions, fmt.Sprintf("%s: %s -> %s (wake_at was %s)", agent, row.State, next, pyOptInt(row.WakeAt.Int64, row.WakeAt.Valid)))
	}

	// Agents we know about but have no state row for.
	have := map[string]bool{}
	all, err := db.AllAgentStates(r.Q)
	if err != nil {
		return actions, err
	}
	for _, s := range all {
		have[s.Agent] = true
	}
	var fresh []string
	for _, a := range r.Ports.Agents() {
		if !have[a] {
			fresh = append(fresh, a)
		}
	}
	sort.Strings(fresh)
	for _, a := range fresh {
		if err := db.SetAgentState(r.Q, a, states.Idle, 0, "registered, first seen"); err != nil {
			return actions, err
		}
		actions = append(actions, a+": IDLE (first seen)")
	}
	for _, a := range actions {
		r.logf("  reconcile: %s", a)
	}
	return actions, nil
}

func pyOptInt(n int64, ok bool) string {
	if !ok {
		return "None"
	}
	return fmt.Sprint(n)
}

// closeStall resolves the decisions a lifted stall raised, then the stall: decision first, row second, so a lift whose close failed
// does not strand the decision, and a kept stall is retried on the next tick.
func (r *Reconciler) closeStall(agent string, actions *[]string, prefixes []string, what string) error {
	for _, prefix := range prefixes {
		ids, err := db.OpenDecisionCardIDs(r.Q, agent, prefix, nil)
		if err != nil {
			return err
		}
		for _, id := range ids {
			if err := r.Ports.Resolve(id, "cleared - its "+what+" is answered"); err != nil {
				r.logf("  %s: could not close decision %d (%v); the stall stays", agent, id, err)
			}
		}
	}
	for _, prefix := range prefixes {
		ids, err := db.OpenDecisionCardIDs(r.Q, agent, prefix, nil)
		if err != nil {
			return err
		}
		if len(ids) > 0 {
			return nil
		}
	}
	if err := db.SetAgentState(r.Q, agent, states.Idle, 0, "the "+what+" is answered; the stall is lifted"); err != nil {
		return err
	}
	*actions = append(*actions, fmt.Sprintf("%s: STALLED -> IDLE (its %s is answered)", agent, what))
	r.logf("  %s: its %s is answered; the stall is lifted", agent, what)
	return nil
}

// Sweep asks the world what is true and corrects the record where it is not. It corrects only what it observed rather than inferred:
// a terminal Orca does not list is gone, and a session with no process is not running. Where the only evidence is a probe it wants
// two witnesses before it moves.
func (r *Reconciler) Sweep(actions *[]string) error {
	names := r.Ports.Agents()
	terminals := map[string]string{}
	for _, a := range names {
		t, err := db.Terminal(r.Q, a)
		if err != nil {
			return err
		}
		terminals[a] = t
	}
	seen := r.Ports.Observe(names, terminals)
	r.Ports.NoteSeen(seen, time.Now())
	type suspect struct {
		agent string
		row   *db.AgentState
	}
	var suspects []suspect

	for _, agent := range names {
		row, err := db.GetAgentState(r.Q, agent)
		if err != nil {
			return err
		}
		if row == nil {
			continue
		}
		if r.structured(agent) {
			if err := r.sweepStructured(agent, row, actions); err != nil {
				return err
			}
			continue
		}
		o := seen[agent]
		handle := row.Terminal.String
		closed := row.ClosedAt.Valid && row.ClosedAt.Int64 != 0

		// The house is gone: nothing may be delivered to it and nothing closed in it, and both key on having a terminal, so dropping
		// the handle is the whole correction. The worktree stays: it is the only record of where this agent's work lives.
		if o.TerminalAlive != nil && !*o.TerminalAlive && handle != "" {
			if err := db.SetTerminal(r.Q, agent, "", row.Worktree.String); err != nil {
				return err
			}
			if _, err := db.AddEvent(r.Q, agent, "terminal_gone", db.KV{K: "handle", V: handle}); err != nil {
				return err
			}
			said := agent + ": terminal is gone; handle dropped"
			if !closed {
				sid := r.Ports.ResumableSession(r.Q, agent)
				if err := db.SetClosed(r.Q, agent, sid); err != nil {
					return err
				}
				if row.State == states.Running {
					if err := db.SetAgentState(r.Q, agent, states.Idle, 0, "terminal gone"); err != nil {
						return err
					}
				}
				said += "; recorded as closed (resume " + orUnknown(sid) + ")"
			}
			*actions = append(*actions, said)
			r.Log("  " + said)
			r.Ports.RefreshView(r.Q, agent)
			continue
		}

		// Whether a session is running is only answerable about an agent whose terminal was just confirmed; without that, "no process"
		// is as likely to mean "we could not look".
		if o.TerminalAlive == nil || !*o.TerminalAlive || !o.SessionSeen {
			continue
		}
		alive := o.SessionPID != nil

		if alive && row.State == states.Stalled && strings.Contains(row.Detail.String, TrustStall) {
			if r.Ports.Term.TrustPromptGone(handle) {
				if err := r.closeStall(agent, actions, TrustStallPrefixes(agent), "folder-trust prompt"); err != nil {
					return err
				}
			}
			if row, err = db.GetAgentState(r.Q, agent); err != nil {
				return err
			}
		}
		if alive && row.State == states.Stalled && strings.Contains(row.Detail.String, DialogStall) {
			seenB := r.Ports.Term.ScreenBlocker(handle)
			if seenB == nil || seenB.Kind == term.Backgrounded {
				if err := r.closeStall(agent, actions, []string{DialogDecision}, DialogStall); err != nil {
					return err
				}
			}
			if row, err = db.GetAgentState(r.Q, agent); err != nil {
				return err
			}
		}
		if alive && row.State == states.Stalled {
			r.Ports.LiftLogin(r.Q, agent, row, actions)
			if row, err = db.GetAgentState(r.Q, agent); err != nil {
				return err
			}
		}

		switch {
		case alive && closed:
			// It came back without us - a human relaunched it, or a resume landed that we never recorded. Believing our own note would
			// close it a second time on top of a live session.
			if err := db.ClearClosed(r.Q, agent); err != nil {
				return err
			}
			if row, err = db.GetAgentState(r.Q, agent); err != nil {
				return err
			}
			*actions = append(*actions, agent+": a session is running; the closed mark was wrong")
			r.logf("  %s: running again (pid %d); cleared the closed mark", agent, *o.SessionPID)
			// Judged before the streak is reset: the reset is what refutes it. Not a trust or dialog stall: a session sitting at the
			// prompt is running too, and only the screen refutes those.
			wakeStall := row.State == states.Stalled && row.WakeFailures >= states.MaxFailedAttempts &&
				!strings.Contains(row.Detail.String, TrustStall) && !strings.Contains(row.Detail.String, DialogStall)
			if row.WakeFailures != 0 {
				if err := db.ResetCounter(r.Q, agent, "wake_failures"); err != nil {
					return err
				}
			}
			if wakeStall {
				// Our launches started nothing and a session is running now; leaving the stall would hide a live agent from delivery.
				if err := db.SetAgentState(r.Q, agent, states.Idle, 0, "a session is running; the wake stall is lifted"); err != nil {
					return err
				}
				*actions = append(*actions, agent+": STALLED -> IDLE (it is running after all)")
			}
		case !alive && !closed && handle != "" && r.Ports.StillDrawn(handle):
			// The table lists no session but the screen draws one: the table missed a live session, and recording it closed would have a
			// wake type a launch line at it. Left as it is; the next reading either lists it or the screen clears.
			if r.saidDrawn == nil {
				r.saidDrawn = map[string]string{}
			}
			if r.saidDrawn[agent] != handle {
				r.saidDrawn[agent] = handle
				r.logf("  %s: the process table lists no session but one is drawn on its terminal; not recorded as closed", agent)
			}
		case !alive && !closed && handle != "":
			// It died, or a human ended it. Recording it closed is what makes work arriving wake it instead of being typed at bash.
			sid := r.Ports.ResumableSession(r.Q, agent)
			if err := db.SetClosed(r.Q, agent, sid); err != nil {
				return err
			}
			if row.State == states.Running {
				if err := db.SetAgentState(r.Q, agent, states.Idle, 0, "session ended without us"); err != nil {
					return err
				}
			}
			var sv any
			if sid != "" {
				sv = sid
			}
			if _, err := db.AddEvent(r.Q, agent, "session_gone", db.KV{K: "session", V: sv}); err != nil {
				return err
			}
			*actions = append(*actions, agent+": its session is gone; recorded as closed")
			r.logf("  %s: session gone; recorded as closed (resume %s)", agent, orUnknown(sid))
			r.Ports.RefreshView(r.Q, agent)
		case alive && row.State == states.Running:
			suspects = append(suspects, suspect{agent, row})
		}
	}

	// The second witness is the only thing here that costs: a "not idle" answer takes the probe's full timeout. One per tick bounds the
	// tick at one probe however many are stuck, round-robin so a merely busy agent cannot hold the slot from the stuck one behind it.
	if len(suspects) > 0 {
		s := suspects[r.probeTurn%len(suspects)]
		r.probeTurn++
		return r.unstickRunning(s.agent, s.row, actions)
	}
	return nil
}

func orUnknown(s string) string {
	if s == "" {
		return "unknown"
	}
	return s
}

// SilentMinutes is how long this agent has read RUNNING with nothing heard, if too long: the first of the two witnesses, and the only
// one that costs nothing. -1 when it is not RUNNING, has no activity, or has not been silent for StuckRunningS.
func SilentMinutes(q db.Q, agent string, row *db.AgentState, now int64) (int64, error) {
	if row == nil || row.State != states.Running {
		return -1, nil
	}
	last, ok, err := db.LastActivity(q, agent)
	if err != nil || !ok {
		return -1, err
	}
	silent := now - last
	if silent >= states.StuckRunningS {
		return silent / 60, nil
	}
	return -1, nil
}

// unstickRunning: RUNNING is the label that takes an agent out of service, so one that entered it and never left is ignored by the
// daemon while every screen reports it working. This correction rests on a probe, not a fact, and getting it wrong closes an agent
// that is working - so it wants two witnesses that share no source: our own events say nothing has been heard for longer than a
// session may idle, and Orca says the terminal is not mid-turn.
func (r *Reconciler) unstickRunning(agent string, row *db.AgentState, actions *[]string) error {
	mins, err := SilentMinutes(r.Q, agent, row, r.now())
	if err != nil || mins < 0 {
		return err
	}
	handle := row.Terminal.String
	t := r.Ports.Term
	if handle == "" || !t.Available() || !t.IdleNow(handle) {
		return nil
	}
	// idle_now reads a permission prompt or a dialog as idle too; an agent at an unanswered prompt is legitimately waiting on the one
	// thing that has its own path to the founder, and flipping it to IDLE would let delivery type new work over it.
	if t.ScreenBlocker(handle) != nil {
		return nil
	}
	if err := db.SetAgentState(r.Q, agent, states.Idle, 0, fmt.Sprintf("RUNNING with nothing heard for %dm, and idle", mins)); err != nil {
		return err
	}
	if _, err := db.AddEvent(r.Q, agent, "unstuck", db.KV{K: "was", V: states.Running}, db.KV{K: "silent_minutes", V: mins}); err != nil {
		return err
	}
	*actions = append(*actions, fmt.Sprintf("%s: RUNNING for %dm with nothing heard; back to IDLE", agent, mins))
	r.logf("  %s: stuck in RUNNING for %dm and idle at its prompt - back to IDLE", agent, mins)
	return nil
}

func (r *Reconciler) structured(agent string) bool {
	return r.Ports.Run != nil && r.Ports.Run.Structured(agent)
}

// sweepStructured compares a structured agent's record with its process, which the daemon owns: there is no terminal to look at and
// nothing to probe twice, so one reading of the process is the evidence. After a daemon restart no process is alive, and every agent that
// was running is recorded closed with its stored session, which the wake resumes.
func (r *Reconciler) sweepStructured(agent string, row *db.AgentState, actions *[]string) error {
	alive := r.Ports.Run.Alive(agent)
	closed := isClosed(row)
	switch {
	case alive && closed:
		if err := db.ClearClosed(r.Q, agent); err != nil {
			return err
		}
		if err := db.SetResumeSession(r.Q, agent, row.ResumeSession.String); err != nil {
			return err
		}
		if row.WakeFailures != 0 {
			if err := db.ResetCounter(r.Q, agent, "wake_failures"); err != nil {
				return err
			}
		}
		if row.State == states.Stalled && row.WakeFailures >= states.MaxFailedAttempts {
			if err := db.SetAgentState(r.Q, agent, states.Idle, 0, "a session is running; the wake stall is lifted"); err != nil {
				return err
			}
			*actions = append(*actions, agent+": STALLED -> IDLE (it is running after all)")
		}
		*actions = append(*actions, agent+": a session is running; the closed mark was wrong")
		r.logf("  %s: running again; cleared the closed mark", agent)
	case !alive && !closed && row.Terminal.String != "":
		// A terminal session still holds the record, the one the agent had before it was switched: not ours to call gone, and recording it
		// closed would have the next wake resume its session id beside it. Closing it in the Python daemon is what hands the agent over.
	case !alive && !closed && row.Worktree.String != "":
		sid := row.ResumeSession.String
		if err := db.SetClosed(r.Q, agent, sid); err != nil {
			return err
		}
		if row.State == states.Running {
			if err := db.SetAgentState(r.Q, agent, states.Idle, 0, "session ended without us"); err != nil {
				return err
			}
		}
		if _, err := db.AddEvent(r.Q, agent, "session_gone", db.KV{K: "session", V: nilIfEmpty(sid)}); err != nil {
			return err
		}
		*actions = append(*actions, agent+": its session is gone; recorded as closed")
		r.logf("  %s: session gone; recorded as closed (resume %s)", agent, orUnknown(sid))
		r.Ports.RefreshView(r.Q, agent)
	case alive && row.State == states.Running:
		if stopped, err := r.stopRetrying(agent, actions); err != nil || stopped {
			return err
		}
		return r.unstickStructured(agent, row, actions)
	}
	return nil
}

// APIRetryLimitS is how long the CLI may retry an unreachable model API (it gives up itself after about 3 minutes) before the turn is
// interrupted. Not the agent's failure, so nothing is counted against it.
const APIRetryLimitS = 300

// stopRetrying interrupts a turn that has done nothing but retry the model call past APIRetryLimitS.
func (r *Reconciler) stopRetrying(agent string, actions *[]string) (bool, error) {
	since := r.Ports.Run.RetryingSince(agent)
	if since == 0 || r.now()-since < APIRetryLimitS {
		return false, nil
	}
	if held, err := r.recentlyInterrupted(agent); held || err != nil {
		return true, err
	}
	if err := r.Ports.Run.Interrupt(agent); err != nil {
		*actions = append(*actions, fmt.Sprintf("%s: the model API has been unreachable since %d; the interrupt failed: %v", agent, since, err))
		return true, nil
	}
	if _, err := db.AddEvent(r.Q, agent, "api_unreachable", db.KV{K: "since", V: since}); err != nil {
		return true, err
	}
	*actions = append(*actions, agent+": the model API has been unreachable for "+fmt.Sprint((r.now()-since)/60)+"m; turn interrupted")
	r.logf("  %s: model API unreachable since %d - interrupted its turn", agent, since)
	return true, nil
}

// unstickStructured: RUNNING with nothing heard for StuckRunningS. The CLI says whether a turn runs: if it does not, the label is
// wrong and is corrected; if it does, the turn has stalled and is interrupted by control request, not by keys.
func (r *Reconciler) unstickStructured(agent string, row *db.AgentState, actions *[]string) error {
	mins, err := SilentMinutes(r.Q, agent, row, r.now())
	if err != nil || mins < 0 {
		return err
	}
	if r.Ports.Run.Busy(agent) {
		if held, err := r.recentlyInterrupted(agent); held || err != nil {
			return err
		}
		if err := r.Ports.Run.Interrupt(agent); err != nil {
			*actions = append(*actions, fmt.Sprintf("%s: RUNNING for %dm with nothing heard; the interrupt failed: %v", agent, mins, err))
			return nil
		}
		if _, err := db.AddEvent(r.Q, agent, "interrupted", db.KV{K: "silent_minutes", V: mins}); err != nil {
			return err
		}
		*actions = append(*actions, fmt.Sprintf("%s: RUNNING for %dm with nothing heard; turn interrupted", agent, mins))
		r.logf("  %s: no frames for %dm - interrupted its turn", agent, mins)
		return nil
	}
	if err := db.SetAgentState(r.Q, agent, states.Idle, 0, fmt.Sprintf("RUNNING with nothing heard for %dm, and idle", mins)); err != nil {
		return err
	}
	if _, err := db.AddEvent(r.Q, agent, "unstuck", db.KV{K: "was", V: states.Running}, db.KV{K: "silent_minutes", V: mins}); err != nil {
		return err
	}
	*actions = append(*actions, fmt.Sprintf("%s: RUNNING for %dm with nothing heard; back to IDLE", agent, mins))
	r.logf("  %s: stuck in RUNNING for %dm and idle - back to IDLE", agent, mins)
	return nil
}

// InterruptHoldS is how long after interrupting a turn the sweep leaves it alone: the CLI takes a moment to end the turn, and an
// interrupt every sweep would be one more control request each time.
const InterruptHoldS = 300

func (r *Reconciler) recentlyInterrupted(agent string) (bool, error) {
	for _, kind := range []string{"interrupted", "api_unreachable"} {
		at, ok, err := db.LastEventTS(r.Q, agent, kind)
		if err != nil {
			return false, err
		}
		if ok && r.now()-at < InterruptHoldS {
			return true, nil
		}
	}
	return false, nil
}
