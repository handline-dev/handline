package lifecycle

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
)

var shellSafe = regexp.MustCompile(`^[A-Za-z0-9_@%+=:,./-]+$`)

// shlexQuote is shlex.quote.
func shlexQuote(s string) string {
	if s == "" {
		return "''"
	}
	if shellSafe.MatchString(s) {
		return s
	}
	return "'" + strings.ReplaceAll(s, "'", `'"'"'`) + "'"
}

// RestartNotice is the line a restarted agent opens with: who restarted it and how, and the settings it was restarted to apply. A
// restart usually means something went wrong, so it is told to retry that.
func RestartNotice(by string, resumed bool, settings []string) string {
	applying := ""
	if len(settings) > 0 {
		applying = " to apply changed settings: " + strings.Join(settings, ", ")
	}
	how := "fresh"
	if resumed {
		how = "resumed"
	}
	return fmt.Sprintf("Restarted by %s (%s)%s. If something was going wrong before this, retry it.", by, how, applying)
}

// WakePromptIn is what wake_prompt is given beyond the agent and whether it resumed.
type WakePromptIn struct {
	WasClosed   bool
	MovedTo     string
	Cold        bool
	Waiting     string // what was waiting for a resume, "" for none
	RestartNote string
}

func joinNonEmpty(parts ...string) string {
	var out []string
	for _, p := range parts {
		if p != "" {
			out = append(out, p)
		}
	}
	return strings.Join(out, "\n\n")
}

// WakePrompt is what an agent is told when it comes back. A resume is started for a message and is handed that message, nothing else.
// One a person asked for, with nothing waiting, is handed nothing: the prompt file is empty, which the launch still takes (that is how a
// launch is known to be ours) and starts no turn from. A resumed session already holds everything it knew, so it is told where it is
// rather than what it did. One that was parked is given its handoff here; one started fresh gets it in its system prompt, and is told
// which. A session that has no record is told so.
func (m *Manager) WakePrompt(agent string, resumed bool, in WakePromptIn) (text string, err error) {
	defer catch(&err)
	var parts []string
	record := ""
	if in.RestartNote != "" && !resumed {
		parts = append(parts, in.RestartNote)
	}
	switch {
	case in.MovedTo != "":
		// A move is not a close. Its handoff was taken as its session ended, and the launch has already put it in this session's system prompt.
		parts = append(parts, fmt.Sprintf("Your account was at its 5h limit, so this session was started on %s. Your handoff is in your system prompt above; carry on from there.", in.MovedTo))
	case resumed:
		return joinNonEmpty(in.RestartNote, in.Waiting), nil
	case in.Cold:
		// The handoff is in the system prompt, and carries the transcript's path: nothing more is appended for it below.
		record = must(db.GetHandoff(m.Q, agent))
		parts = append(parts, "Your session was closed while idle and this is a fresh one. Your handoff is in your system prompt above; pick the task back up from it.")
	case in.WasClosed:
		// Taking the parked branch here would tell an agent that was never parked that its context was cleared.
		record = must(db.GetHandoff(m.Q, agent))
		if record != "" {
			parts = append(parts, "Your session could not be resumed, so this is a fresh one. Your handoff is in your system prompt above.")
		} else {
			parts = append(parts, "Your session could not be resumed, so this is a fresh one. There is no handoff. Read your journal and your inbox before carrying on.")
			if note := m.Handoff.MemoryLine(agent); note != "" {
				parts = append(parts, note)
			}
		}
	default:
		parts = append(parts, "You were parked; this is a fresh session and your context is empty. Your handoff record follows. If it names a request or task, carry on with it; if it says none, wait - the next item is typed to you when it comes.")
		record = must(db.GetHandoff(m.Q, agent))
		if record != "" {
			parts = append(parts, record)
		}
	}
	if record == "" {
		// Nothing carried the request or the transcript, so this says both, finding the path by the session's id when the status names none.
		session, path := m.Handoff.PreviousSession(m.Q, agent, m.Agents.CurrentAccount(m.Q, agent))
		if in.MovedTo == "" {
			parts = append(parts, m.Handoff.RequestLine(m.Q, agent))
		}
		if path != "" {
			parts = append(parts, fmt.Sprintf("Your previous session is %s, recorded at %s\nGrep it for what you need - do not read it whole, it is a JSONL transcript with every tool result in it, and reading it back would undo the point of parking you.", session, path))
		} else if in.MovedTo == "" {
			parts = append(parts, "Your previous session's transcript could not be found; read your journal and your inbox to see where you were.")
		}
	}
	return strings.Join(parts, "\n\n"), nil
}

// StartForWork gives an agent somewhere to run, because work is waiting for it. Counted against the same streak a failed wake is: a repo
// that will never check out would otherwise be retried every tick for ever. After MaxFailedAttempts it stalls and says so.
func (m *Manager) StartForWork(agent string) (said string, err error) {
	defer catch(&err)
	row := must(db.GetAgentState(m.Q, agent))
	if m.ResumableWithoutTerminal(row) {
		// The caller wakes it, opening the terminal and resuming the session; starting it here would lose that.
		return agent + ": closed with a session to resume - a wake brings it back", nil
	}
	repo := m.Agents.RepoOf(agent)
	if repo == "" {
		asked := len(must(db.QueuedFor(m.Q, agent)))
		answers := len(must(db.UnannouncedAnswers(m.Q, agent)))
		var what []string
		if asked > 0 {
			what = append(what, fmt.Sprintf("%d request(s)", asked))
		}
		if answers > 0 {
			what = append(what, fmt.Sprintf("%d answer(s)", answers))
		}
		w := strings.Join(what, ", ")
		if w == "" {
			w = "work"
		}
		return fmt.Sprintf("%s: %s waiting and it has no terminal - set `repo:` in its agent.yaml, or start it by hand (t)", agent, w), nil
	}
	// With no room `hl launch` would refuse in the new terminal, and the refusal would count against the agent though it is the accounts
	// that are full.
	if !m.Launch.ChooseAccount(m.Q, agent) {
		return fmt.Sprintf("%s: not started - %s", agent, m.Launch.NoRoom(agent)), nil
	}
	if row != nil && row.WakeFailures >= states.MaxFailedAttempts {
		if row.State == states.Stalled {
			return fmt.Sprintf("%s: not started again - %d attempts have failed", agent, row.WakeFailures), nil
		}
		// Latched IDLE by a daemon that did not stall it: one more try, which starts it or fails into the stall below with the reason.
		check(db.ResetCounter(m.Q, agent, "wake_failures"))
		check(db.BumpCounter(m.Q, agent, "wake_failures", states.MaxFailedAttempts-1))
	}
	// Its own worktree if it still has one - a terminal can be closed by a human or lost when Orca restarts without anything happening to
	// the branch or to what is uncommitted in it, and a second worktree would strand the first.
	wt := ""
	if row != nil {
		wt = m.Spawn.WorktreePath(row.Worktree.String)
	}
	// ok is StartIn/Spawn's own answer, not re-derived from the row: they confirm a session actually started now, so a terminal left
	// standing with nothing in it counts as the failed wake it is.
	var ok bool
	if wt != "" {
		ok, said, err = m.Spawn.StartIn(agent, wt, "")
	} else {
		ok, said, err = m.Spawn.Spawn(agent, realpath(expandUser(repo)))
	}
	// A start that could not be tried (git cannot run, no descriptors) is no failed start of the agent's own: it is not counted.
	check(err)
	first := strings.SplitN(said, "\n", 2)[0]
	if ok {
		check(db.ResetCounter(m.Q, agent, "wake_failures"))
		must(db.AddEvent(m.Q, agent, "started_for_work", db.KV{K: "repo", V: repo}))
		m.Log("  %s: started in %s - work was waiting for it", agent, repo)
		return fmt.Sprintf("%s: started for work waiting - %s", agent, first), nil
	}
	check(db.BumpCounter(m.Q, agent, "wake_failures", 1))
	m.Log("  %s: could not start for waiting work: %s", agent, first)
	if fresh := must(db.GetAgentState(m.Q, agent)); fresh != nil && fresh.WakeFailures >= states.MaxFailedAttempts {
		check(db.SetAgentState(m.Q, agent, states.Stalled, 0, fmt.Sprintf("%d starts for waiting work failed: %s", fresh.WakeFailures, first)))
		m.Log("  %s: STALLED after %d failed starts", agent, fresh.WakeFailures)
	}
	return fmt.Sprintf("%s: could not be started - %s", agent, first), nil
}

func (m *Manager) writeWakePrompt(agent, text, suffix string) (string, error) {
	d := filepath.Join(m.Home, "wake")
	if err := os.MkdirAll(d, 0o755); err != nil {
		return "", err
	}
	p := filepath.Join(d, agent+"."+suffix)
	return p, os.WriteFile(p, []byte(text), 0o644)
}

// WakeOpts say why and how a session is brought back. Force is a human asking for the session: a closed agent is brought back with
// nothing waiting for it. Warm resumes the session whatever its prompt cache says. Cause is what the start is recorded as ("wake",
// "fresh close" or "move" when empty). RestartedBy names who asked for a restart: the session's first prompt says so. MovedTo names the
// account this wake starts the agent on when it is not the one the last session was on.
type WakeOpts struct {
	Force, Warm bool
	Cause       string
	RestartedBy string
	Applying    []string
	MovedTo     string
}

// WakeAgent hands a cleared agent back its own handoff so it can carry on. The agent kept its account across the park, and the handoff is
// what it knows when it wakes. A closed agent has no terminal; a new one is opened for it in its worktree.
func (m *Manager) WakeAgent(agent string, o WakeOpts) (ok bool, said string, err error) {
	defer catch(&err)
	if m.structured(agent) {
		// No pane to type into: no terminal lock or input hold, one lifecycle act at a time instead.
		defer m.lockAgent(agent)()
		ok, said = m.wakeAgent(agent, o)
		return ok, said, nil
	}
	if held := m.Typing.HeldByOther(m.Q, agent); held != "" {
		// nothing is typed into a pane someone holds, a launch line included; the work stays queued
		return false, m.Typing.HeldWhy(agent, held), nil
	}
	release, mine := m.Typing.TakeTerminal(agent)
	if !mine {
		// A human's key and a tick can both land here, and this one holds the terminal for as long as a session takes to appear.
		return false, TypingElsewhere + "; not waking twice", nil
	}
	defer release()
	// Not the type gate: closed means no live process to be mid-turn in, and the sub-paths below that do meet a live one probe it themselves.
	ok, said = m.wakeAgent(agent, o)
	if ok {
		check(m.Delivery.MarkTyped(m.Q, agent, nil))
	}
	return ok, said, nil
}

// WakeAgentLocked is a wake for callers already holding the terminal lock.
func (m *Manager) WakeAgentLocked(agent string, o WakeOpts) (ok bool, said string, err error) {
	defer catch(&err)
	if m.structured(agent) {
		defer m.lockAgent(agent)()
	}
	ok, said = m.wakeAgent(agent, o)
	return ok, said, nil
}

// liveSessionsSettled reads the process table again after a pause while it misses.
func (m *Manager) liveSessionsSettled() (map[string]int, bool) {
	for _, pause := range TableRetryPauses {
		if s, ok := m.Observe.LiveSessions(); ok {
			return s, true
		}
		m.sleep(pause)
	}
	return m.Observe.LiveSessions()
}

func newHandoffID() string {
	b := make([]byte, 6)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

func isClosed(row *db.AgentState) bool {
	return row != nil && row.ClosedAt.Valid && row.ClosedAt.Int64 != 0
}

func (m *Manager) wakeAgent(agent string, o WakeOpts) (bool, string) {
	handle := must(db.Terminal(m.Q, agent))
	row := must(db.GetAgentState(m.Q, agent))
	closed := isClosed(row)
	if m.structured(agent) {
		return m.wakeStructured(agent, row, o)
	}
	// A closed session's pane was closed with it; the wake opens a new terminal in the worktree the agent kept.
	wt := ""
	if row != nil {
		wt = m.Spawn.WorktreePath(row.Worktree.String)
	}
	if !m.Term.Available() || !(handle != "" || (closed && wt != "")) {
		return false, "no terminal to wake"
	}
	if !closed && row != nil && must(m.Delivery.SessionPresent(agent)) {
		// A drifted session is ended and takes the closed agent's wake path below, with its own session id to resume. Ending it is only for
		// the resume that follows, and nothing resumes without a message; with none waiting it is woken in place as before.
		if m.Launch.Drifted(agent, row) && m.waitingFor(agent) {
			st := m.Status.ReadForAgent(agent, m.Agents.CurrentAccount(m.Q, agent))
			switch {
			case st.SessionID == "":
				m.Log("  %s: launch recipe changed but there is no session id to resume - waking it in place", agent)
			default:
				if ended, _ := m.endSession(agent, st.SessionID, st, true, false); !ended {
					m.Log("  %s: launch recipe changed but its session would not end - waking it in place", agent)
				} else {
					m.Log("  %s: launch recipe changed since this session started - relaunching instead of waking it in place", agent)
					row = must(db.GetAgentState(m.Q, agent))
					closed = isClosed(row)
				}
			}
		}
	}
	switch {
	case closed:
		return m.wakeClosed(agent, row, handle, wt, o)
	case must(db.GetHandoff(m.Q, agent)) == "":
		return false, "no handoff to resume from"
	case !must(m.Delivery.SessionPresent(agent)):
		// Not the closed branch: nothing was relaunched, so the row's claim that a session is there is the only evidence - and it is the
		// claim that goes wrong. A handoff typed at a shell prompt is executed line by line. The sweep records this agent closed on its next
		// tick, and the branch above then relaunches it before anything is typed.
		return false, "no session process; not typing the handoff at a shell prompt"
	}
	return m.wakeInPlace(agent, handle, o)
}

// wakeInPlace types a pointer at the handoff into the session a /clear started.
func (m *Manager) wakeInPlace(agent, handle string, o WakeOpts) (bool, string) {
	if ok, why := m.clearTrustPrompt(agent, handle, false); !ok {
		return false, why
	}
	if !m.Term.TypeableNow(handle) {
		return false, "the session is busy or has a modal open; not typing the handoff"
	}
	// The record is a page of lines, and typed at a session that many arrive inside <pasted_content> to be declined. It is written to a
	// file the session reads, and one line pointing at it is typed. Not the launch's prompt file: that one is taken and deleted by the launch.
	recordText, err := m.WakePrompt(agent, false, WakePromptIn{MovedTo: o.MovedTo})
	check(err)
	record, err := m.writeWakePrompt(agent, recordText, "handoff.txt")
	check(err)
	pointer := fmt.Sprintf("You were parked; this is a fresh session and your context is empty. Your handoff is in %s - read all of it. If it names a request or task, carry on with it; if it says none, wait - the next item is typed to you when it comes.", record)
	ok, why := m.Term.ResumeWith(handle, pointer)
	if ok {
		// The session stays the one the handoff's /clear started, so there is no launch to keep this.
		st := m.Status.ReadForAgent(agent, m.Agents.CurrentAccount(m.Q, agent))
		must(db.NoteSessionStart(m.Q, agent, "handoff", false, st.SessionID, recordText, pointer, ""))
		check(db.ClearHandoff(m.Q, agent))
	}
	m.Log("  %s: %s", agent, why)
	return ok, why
}

// wakeClosed brings a closed agent back: its shell is put back into the session, resuming the same one first (which is what makes
// closing cheap) and a fresh start the fallback, not the plan.
func (m *Manager) wakeClosed(agent string, row *db.AgentState, handle, wt string, o WakeOpts) (bool, string) {
	force, warm, cause, restartedBy, movedTo := o.Force, o.Warm, o.Cause, o.RestartedBy, o.MovedTo
	if owed := m.restartOwed(agent, row); owed != nil {
		// The restart that closed it is finished by this wake, as it was asked: same conversation unless it was fresh, same requester. A
		// person's own force is not made to wait.
		var left int64
		if !force {
			var err error
			left, err = m.RestartRetryWait(agent, row, m.now())
			check(err)
		}
		if left != 0 {
			return false, "its restart failed; trying again shortly"
		}
		fresh, _ := owed["fresh"].(bool)
		by, _ := owed["by"].(string)
		oc, _ := owed["cause"].(string)
		force, warm = true, !fresh
		if restartedBy == "" {
			restartedBy = by
		}
		if cause == "" {
			cause = oc
		}
	}
	// Before anything is typed: an agent.yaml that will not read is this agent's config error, which the callers stall by name.
	if err := m.Agents.ReadConfig(agent); err != nil {
		panic(&ConfigError{err})
	}
	// Nothing ever delivers to a closed agent, so nothing has enforced its edges either - do it before waitingFor, which would otherwise
	// not count a request still edge-unconfirmed.
	must(m.Delivery.EnforceEdges(m.Q, agent))
	// A closed agent is brought back for a message. With none, there is nothing to tell it and no reason to spend a session on it; the
	// message that arrives is what wakes it. A move is the exception: the agent was mid-work and its handoff is what it is started with.
	if movedTo == "" && !force && !m.waitingFor(agent) {
		return false, NothingWaiting
	}
	// `hl launch` refuses at the 5h wall, and the daemon can read that before it types anything. Trying anyway would spend the wake
	// timeout to be told what we already knew, and count a streak against an agent whose only problem rolls over by itself. The account
	// this launch lands on is the one a session to resume lives under, so an agent whose own account is at the wall waits for its window
	// rather than losing its conversation - a fresh start elsewhere comes back with only the handoff.
	session := row.ResumeSession.String
	wait := m.Launch.WakeWait(m.Q, agent, row)
	if wait != nil && wait.Slot {
		// No wall, no slot. While the prompt cache is warm a resume is free, so it waits for a slot here; once the cache is cold a fresh
		// start costs only the handoff, so it moves to the account furthest behind its week. With nowhere to go it waits.
		cold := movedTo == "" && !warm && session != "" && m.cacheExpiredAt(agent, session, m.now())
		to := ""
		if cold {
			to = m.Launch.SlowestPaced(m.Q, agent, row, wait.Account)
		}
		if to == "" || !m.startFreshElsewhere(agent, session, wait.Account, to, fmt.Sprintf("%s is full and its cache is cold", wait.Account)) {
			return false, m.Launch.DescribeWakeWait(wait)
		}
		session, movedTo, wait = "", to, nil
	}
	if wait != nil {
		// The resume account is at the wall. If another account has room, start fresh there: a resume id cannot follow an account change.
		// The handoff is the record the close took while the session was live; a snapshot read now is stale by construction. One that is
		// missing a fact keeps the id, since the session is the only complete copy of the context.
		rescued := false
		acct, pct := wait.Account, wait.Pct
		kind := "usage"
		if wait.IsLong {
			kind = "usage_7d"
		}
		elsewhere, planWhy := "", ""
		if session != "" && !wait.Assumed && !m.Handoff.BackingOff(m.Q, agent) {
			elsewhere, planWhy = m.Handoff.MovePlan(m.Q, agent, kind)
		}
		if elsewhere != "" {
			why := fmt.Sprintf("pinned to %s at %d%% (%s)", acct, pct, wait.Window)
			if m.startFreshElsewhere(agent, session, acct, elsewhere, why) {
				session, movedTo, rescued = "", elsewhere, true
			}
		}
		if !rescued {
			// Returned, not logged: this is reached on every retry for as long as the window lasts, and each caller says it in its own
			// place. The reason it did not move rides along, so the wait does not read as if nothing had looked elsewhere.
			msg := m.Launch.DescribeWakeWait(wait)
			if planWhy != "" {
				msg += "; not moved: " + planWhy
			}
			return false, msg
		}
	}
	c := closed2{force, warm, cause, restartedBy, movedTo, o.Applying}
	if m.structured(agent) {
		return m.launchStructured(agent, row, wt, session, c)
	}
	return m.launchClosed(agent, row, wt, session, c)
}

// startFreshElsewhere moves a closed agent off the account its session is tied to: the handoff the close took is the record, the closed mark
// goes, and the wake that follows starts fresh on `to`. why is what the log says the agent was stuck on. False when the handoff could not be
// read whole: the session is the only complete copy of the context, so the resume id stays and the wake goes on waiting.
func (m *Manager) startFreshElsewhere(agent, session, from, to, why string) bool {
	hid := newHandoffID()
	check(db.StartHandoff(m.Q, hid, agent, session, "pinned", m.now()+states.HandoffDeadlineS))
	var missing []string
	if must(db.GetHandoff(m.Q, agent)) == "" {
		missing = must(m.Handoff.Record(m.Q, agent, ""))
	}
	if len(missing) > 0 {
		gap := "could not read " + strings.Join(missing, ", ")
		check(db.AdvanceHandoff(m.Q, hid, "released", "degraded", clip300(gap)))
		check(db.BumpCounter(m.Q, agent, "throttle_count", 1))
		m.Log("  %s: handoff degraded (%s); keeping the resume id", agent, gap)
		return false
	}
	check(db.AdvanceHandoff(m.Q, hid, "released", "done", ""))
	check(db.ResetCounter(m.Q, agent, "throttle_count"))
	check(db.SetClosed(m.Q, agent, ""))
	must(db.AddEvent(m.Q, agent, "moved_account", db.KV{K: "from", V: from}, db.KV{K: "to", V: to}, db.KV{K: "trigger", V: "pinned"}))
	m.Log("  %s: %s - starting fresh on %s", agent, why, to)
	m.Agents.MeasureBegin(m.Q, agent, to)
	return true
}

func clip300(s string) string {
	if r := []rune(s); len(r) > 300 {
		return string(r[:300])
	}
	return s
}

type closed2 struct {
	force, warm        bool
	cause, restartedBy string
	movedTo            string
	applying           []string
}

// launchClosed relaunches a closed agent: a launch command typed at the shell its last session left, or run in a new terminal.
func (m *Manager) launchClosed(agent string, row *db.AgentState, wt, session string, c closed2) (bool, string) {
	closed := true
	fresh, haveFresh := must2(db.LastEventTS(m.Q, agent, "fresh_close"))
	why0 := c.cause
	if why0 == "" {
		switch {
		case c.movedTo != "":
			why0 = "move"
		case haveFresh && fresh >= row.ClosedAt.Int64:
			why0 = "fresh close"
		default:
			why0 = "wake"
		}
	}
	base, err := m.Launch.CommandFor(agent, c.movedTo, why0, c.restartedBy)
	check(err)

	// Resuming the session is what makes closing cheap, so it is tried first - but a resume can be the very thing that fails: an id whose
	// transcript was pruned, or a worktree moved out from under it, gives a launch that refuses every time it is retried. A fresh start in
	// the same terminal loses the context and keeps the agent, which is the better of the two ends this can have. The wake prompt travels
	// as the session's own first prompt, in a file the launch reads and deletes: typed at the terminal instead, it lands in a bare shell
	// whenever the session dies first, and the shell runs it line by line.
	resumed, foreign, taken := false, false, false
	var why string

	// runningElsewhere is (foreign, why, found). Unreadable is not "none": typing over a session that might be there reaches it as a
	// prompt, so it is refused the same way and for the same reason it is not blamed on the agent's own streak.
	runningElsewhere := func() (bool, string, bool) {
		sessions, ok := m.liveSessionsSettled()
		if !ok {
			return true, Unconfirmed, true
		}
		if sessions[agent] != 0 {
			return !taken, "a session it did not start is running", true
		}
		// The table lists none. Its one failure that matters is a live session missing from it, so the screen is asked too: a launch line
		// typed at a drawn session is a prompt to it.
		if h := must(db.Terminal(m.Q, agent)); h != "" && m.Delivery.SessionStillDrawn(h, false) {
			return true, "the process table lists no session, but one is drawn on the terminal's screen; typed nothing", true
		}
		return false, "", false
	}

	// Past the cache's life a resume costs the whole transcript and a fresh start costs the handoff; the record stays on the row for the
	// launch to inject.
	cold := c.movedTo == "" && !c.warm && m.cacheExpiredAt(agent, session, m.now())
	// A fresh start goes where there is room. With none, `hl launch` would refuse and the refusal would count against the agent's wake
	// attempts, though it is the accounts that are full.
	if c.movedTo == "" && (cold || session == "") && !m.Launch.ChooseAccount(m.Q, agent) {
		return false, m.Launch.NoRoom(agent)
	}
	// The slot is held from here, so a second wake in this tick sees it taken.
	slot := c.movedTo
	if slot == "" {
		slot = m.Launch.AccountFor(m.Q, agent, session != "" && !cold)
	}
	if slot != "" {
		must(db.AddEvent(m.Q, agent, "wake_reserved", db.KV{K: "account", V: slot}))
	}
	took := false
	if cold && must(db.GetHandoff(m.Q, agent)) == "" {
		// The close that would have taken it failed, and the snapshot no longer names the session; the id on the row still does.
		gaps := must(m.Handoff.Record(m.Q, agent, session))
		took = true
		if len(gaps) > 0 {
			w := "could not read " + strings.Join(gaps, ", ")
			// "handoff" must stay out of the activity kinds: this is ocp's own bookkeeping about a failed take, not the agent doing
			// anything, and counting it would read a dead cache warm.
			must(db.AddEvent(m.Q, agent, "handoff", db.KV{K: "degraded", V: true}, db.KV{K: "session", V: nilIfEmpty(session)}, db.KV{K: "why", V: w}))
			m.Log("  %s: cold wake handoff degraded (%s)", agent, w)
		}
	}
	attempts := []bool{false}
	if session != "" && !cold {
		attempts = []bool{true, false}
	}
	var waiting struct {
		text string
		told func(string) error
		ok   bool
	}
	ok := false
	for _, resume := range attempts {
		handle := must(db.Terminal(m.Q, agent))
		// Checked fresh before every send, not only after one fails: the closed branch has no lock on the world outside ocp, and a session
		// can come up here between the first attempt's check and the second's, or before either has run at all. taken still holds the
		// previous attempt's own verdict here: a session already live now is that attempt's own come up late, not foreign, if that attempt's
		// launch took the prompt file.
		if f, w, found := runningElsewhere(); found {
			if f {
				foreign, why = true, w
			}
			break
		}
		// Only a resume reads it: a fresh start has read nothing of what the queue held, and is told the ordinary way once it is up.
		waiting.text, waiting.told, waiting.ok = "", nil, false
		if resume {
			t, told, got := must3(m.Delivery.WaitingPrompt(m.Q, agent))
			waiting.text, waiting.told, waiting.ok = t, told, got
			if !got && !c.force {
				return false, "what was waiting for it is gone; nothing to resume it for"
			}
		}
		restartNote := ""
		if c.restartedBy != "" {
			restartNote = RestartNotice(c.restartedBy, resume, c.applying)
		}
		text, err := m.WakePrompt(agent, resume, WakePromptIn{WasClosed: closed, MovedTo: c.movedTo, Cold: cold, Waiting: waiting.text, RestartNote: restartNote})
		check(err)
		pf, err := m.writeWakePrompt(agent, text, "txt")
		check(err)
		cmd := base + " --prompt-file " + shlexQuote(pf)
		if resume {
			cmd += " -- --resume " + session
		}
		var launched bool
		launched, why = m.launchInPane(agent, handle, wt, cmd, func() bool { return m.tookPrompt(agent, pf) })
		if launched {
			resumed, ok = resume, true
			break
		}
		_, statErr := os.Stat(pf)
		taken = os.IsNotExist(statErr)
		_ = os.Remove(pf)
		// A session owns the terminal now, and the next attempt's command would be typed into it as a prompt. It is this launch's own,
		// come up late, only if the launch took the prompt.
		if f, w, found := runningElsewhere(); found {
			if f {
				foreign, why = true, w
			}
			break
		}
	}
	if !ok {
		return false, m.wakeFailed(agent, session, why, took, foreign)
	}
	// A relaunch is where claude shows its folder-trust screen - on a moved agent the new account has never trusted this folder - and the
	// wake prompt waits behind it. Until it is past, the agent is not woken.
	handle := must(db.Terminal(m.Q, agent))
	cleared, w := true, ""
	if handle != "" {
		cleared, w = m.clearTrustPrompt(agent, handle, true)
	}
	if !cleared {
		if resumed && waiting.ok {
			// The session took the message as its first prompt and reads it once past the screen. Recorded now, after the closed mark came
			// off, so nothing later voids it and has it told twice.
			check(waiting.told(session))
		}
		return false, w
	}
	check(db.ResetCounter(m.Q, agent, "wake_failures"))
	if f := must(db.GetAgentState(m.Q, agent)); f != nil && isClosed(f) {
		check(db.ClearClosed(m.Q, agent))
	}
	if resumed && waiting.ok {
		// After clear_closed, which voids what a session was told.
		check(waiting.told(session))
	}
	payload := []db.KV{{K: "resumed_session", V: nilIfEmpty(ifTrue(resumed, session))}}
	if cold {
		payload = append(payload, db.KV{K: "cache_expired", V: true})
	}
	must(db.AddEvent(m.Q, agent, "wake", payload...))
	check(db.ClearHandoff(m.Q, agent))
	how := ""
	switch {
	case resumed:
		how = " (session resumed)"
	case cold:
		how = " (fresh - its prompt cache had expired)"
	}
	m.Log("  %s: relaunched with its wake prompt%s", agent, how)
	return true, "relaunched with the wake prompt"
}

// wakeFailed records a wake that started nothing and returns what to say. foreign: a session it did not start is running, which is no
// failed wake to count toward STALLED.
func (m *Manager) wakeFailed(agent, session, why string, took, foreign bool) string {
	if took {
		// A record no session was handed would be typed at whichever live session the next wake finds, as if it had been parked.
		check(db.ClearHandoff(m.Q, agent))
	}
	// The closed mark and resume_session are the whole way back. A wake that started nothing leaves both alone: the next attempt -
	// work arriving, or the next restart - has nothing else to use.
	must(db.AddEvent(m.Q, agent, "wake_failed", db.KV{K: "resume_session", V: nilIfEmpty(session)}, db.KV{K: "why", V: why}))
	if foreign {
		// The agent is running, so this is no failed wake to count toward STALLED; the sweep sees the session and clears the closed mark.
		m.Log("  %s: %s; typed nothing more", agent, why)
		return why
	}
	check(db.BumpCounter(m.Q, agent, "wake_failures", 1))
	m.Log("  %s: %s; still closed", agent, why)
	if f := must(db.GetAgentState(m.Q, agent)); f != nil && f.WakeFailures >= states.MaxFailedAttempts {
		// Work arriving retries the wake every tick, so a launch that will never work is a loop that never ends and never says so.
		// Three launches that started nothing is not the wall - that was turned away above - and not a resume that can be dropped,
		// which the fallback above already did. It waits for a human, or for the sweep to see a session after all.
		check(db.SetAgentState(m.Q, agent, states.Stalled, 0, fmt.Sprintf("%d wakes started no session: %s", f.WakeFailures, why)))
		m.Log("  %s: STALLED after %d failed wakes", agent, f.WakeFailures)
	}
	return why
}

func ifTrue(b bool, s string) string {
	if b {
		return s
	}
	return ""
}

func nilIfEmpty(s string) any {
	if s == "" {
		return nil
	}
	return s
}

// tookPrompt is WaitForSession for a wake's launch, and the closed mark goes the moment a session has taken the prompt: what follows
// holds the terminal until the session's first turn is over (up to two minutes), and a session that is running must not read as closed
// meanwhile.
func (m *Manager) tookPrompt(agent, promptFile string) bool {
	up := m.Delivery.WaitForSession(agent, WakeSessionTimeoutS, promptFile)
	if up {
		check(db.ClearClosed(m.Q, agent))
	}
	return up
}

// launchInPane runs the launch command for agent: typed into the shell its last session left, or - a closed session's pane is closed
// with it - in a new terminal in its worktree. started says whether a typed launch took.
func (m *Manager) launchInPane(agent, handle, wt, cmd string, started func() bool) (bool, string) {
	if handle != "" {
		return m.Term.Relaunch(handle, cmd, started)
	}
	ok, why, err := m.Spawn.StartIn(agent, wt, cmd)
	check(err)
	if ok && !started() {
		return false, "the launch command was run but no session took its prompt"
	}
	if ok {
		if nh := must(db.Terminal(m.Q, agent)); nh != "" {
			m.Term.WaitIdle(nh)
		}
	}
	return ok, why
}
