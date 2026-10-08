package tick

import (
	"fmt"
	"sort"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
)

// Result is one tick: when it ran and what it did.
type Result struct {
	Now     int64
	Actions []string
}

// Tick is daemon/evaluate.py: what one tick decides for each agent and the passes after them. Its fields are the ports; the unexported
// ones are the state Python kept in module variables, which a port must keep per process or its log lines differ after the first tick.
type Tick struct {
	Q   db.Q
	Log func(string)
	Now func() int64

	Host      Host
	Usage     Usage
	Delivery  Delivery
	Messaging Messaging
	Lifecycle Lifecycle
	Handoff   Handoff
	Reconcile Reconciler
	Sweeps    Sweeps
	Passes    []NamedSweep // the guarded passes after the per-agent turns, in order
	Observe   Observe
	Config    Config
	Cost      *CostPilot

	said, saidClose map[string]string
	rolloverDay     string
}

// NamedSweep is one of the tick's guarded passes: its name is how a failure is logged.
type NamedSweep struct {
	Name string
	Run  func(q db.Q, now int64) error
}

func (t *Tick) logf(format string, a ...any) { t.Log(fmt.Sprintf(format, a...)) }

// check and must turn a database error inside one agent's turn into that agent's failure, caught in Run.
type failure struct{ err error }

func check(err error) {
	if err != nil {
		panic(failure{err})
	}
}

func must[T any](v T, err error) T {
	check(err)
	return v
}

func must2[A, B any](a A, b B, err error) (A, B) {
	check(err)
	return a, b
}

// guard runs f and returns the error it panicked with through check, nil otherwise.
func guard(f func()) (err error) {
	defer func() {
		if r := recover(); r != nil {
			if fl, ok := r.(failure); ok {
				err = fl.err
			} else if e, ok := r.(error); ok {
				err = e // a port that fails by panicking is this agent's failure, as any exception was in Python
			} else {
				err = fmt.Errorf("panic: %v", r)
			}
		}
	}()
	f()
	return nil
}

func (t *Tick) init() {
	if t.said == nil {
		t.said, t.saidClose = map[string]string{}, map[string]string{}
	}
}

// QueuedOrIdle writes QUEUED when something has not reached the agent yet, IDLE otherwise - and returns which. Shared by every writer of a
// due wake_at, so QUEUED stays a claim about the queue everywhere.
func QueuedOrIdle(q db.Q, agent, when string) (string, error) {
	n, err := db.UnreachedCount(q, agent)
	if err != nil {
		return "", err
	}
	if n > 0 {
		return states.Queued, db.SetAgentState(q, agent, states.Queued, 0, when)
	}
	return states.Idle, db.SetAgentState(q, agent, states.Idle, 0, when+"; no requests waiting")
}

// confirmedReading is the reading, unless the running process is on another account: the column names where a move sent the agent, the
// process is where it is, so the process wins and the column is set right.
func (t *Tick) confirmedReading(agent string, reading *AccountReading) *AccountReading {
	actual := t.Host.RunningAccount(agent)
	if reading == nil || actual == "" || actual == reading.Account {
		return reading
	}
	t.logf("  %s: recorded on %s but its process is on %s - judging it there", agent, reading.Account, actual)
	must(db.AddEvent(t.Q, agent, "account_mismatch", db.KV{K: "recorded", V: reading.Account}, db.KV{K: "running", V: actual}))
	check(db.SetAccount(t.Q, agent, actual, "recorded"))
	return &AccountReading{actual, "recorded"}
}

func (t *Tick) accountReading(agent string) *AccountReading {
	r, ok := t.Usage.AccountReading(t.Q, agent)
	if !ok {
		return nil
	}
	return &r
}

func closedAt(row *db.AgentState) bool {
	return row != nil && row.ClosedAt.Valid && row.ClosedAt.Int64 != 0
}

// Supervise is one agent's turn in a tick, its own function so that all of it sits inside the single guard in Run that treats a failure
// as this agent's failure.
func (t *Tick) Supervise(agent string, now int64, samples map[string][]Sample, actions *[]string) {
	t.init()
	q := t.Q
	row := must(db.GetAgentState(q, agent))
	state := states.Idle
	if row != nil {
		state = row.State
	}
	if state == states.Stalled {
		return // 8.6: only a human clears this
	}
	if !states.Evaluated[state] {
		return
	}
	// An agent mid-handoff is excluded from trigger evaluation (8.9).
	if h := must(db.InFlightFor(q, agent)); h != nil {
		return
	}
	// A clear held off by background work is settled before anything else is done for the agent.
	if t.Handoff.SettleOwedClear(q, agent) {
		return
	}
	// Whatever a message could not type at once gets one more try every tick, RUNNING included.
	t.Messaging.LandingSweep(q, agent)
	t.Messaging.TellUndelivered(q, agent)
	t.Messaging.DeliverPending(q, agent)

	// The live reading this tick judges the agent against, not the account a resume would use.
	reading := t.confirmedReading(agent, t.accountReading(agent))
	acct := ""
	if reading != nil {
		acct = reading.Account
	}
	st := t.Usage.ReadForAgent(agent, acct)
	ctxLimit := t.Usage.ContextLimit(agent, st.ModelID)

	// An agent whose closed session was restored in this tick is judged on the next one: st was read above, before the wake, and a closed
	// agent's snapshot is stale by construction.
	wokeNow := false

	// Wake anything whose deadline has come due, whatever its state.
	if row != nil && row.WakeAt.Valid && row.WakeAt.Int64 != 0 && row.WakeAt.Int64 <= now &&
		(state == states.Parked || state == states.Throttled || state == states.Degraded) {
		// The streak is NOT cleared here: waking is the normal end of every park. It is cleared by a handoff that actually succeeds.
		restored := closedAt(row)
		if restored {
			// A closed agent's edges are enforced by nothing else.
			check(t.Delivery.EnforceEdges(q, agent))
		}
		var woke bool
		var why string
		if restored && !((t.Host.SessionAlive(agent) || t.Lifecycle.ResumableWithoutTerminal(row)) && t.Lifecycle.WaitingFor(q, agent)) {
			// The deadline is spent and nothing came for it: a closed session is not resumed to be told nothing. It stays closed, and
			// what arrives next is what wakes it.
			woke, restored = false, false
		} else {
			woke, why = t.Lifecycle.WakeAgent(q, agent)
			if restored && woke {
				t.Delivery.NoteFailure(agent, "")
			}
		}
		if restored && !woke {
			t.Delivery.NoteFailure(agent, "could not be woken - "+why)
			// For an agent whose session was closed, the deadline is the only thing that makes the wake due again: clearing it on a wake
			// that did not work is how a park slept through every later tick.
			t.logf("  %s: stays %s, still due", agent, state)
			return
		}
		// The prompt has been sent by now, so the agent may already have answered it: only write QUEUED if nothing has claimed it since.
		if fresh := must(db.GetAgentState(q, agent)); fresh != nil && fresh.State != state {
			return
		}
		newState := must(QueuedOrIdle(q, agent, "wake_at reached"))
		whyWord := "nothing queued"
		if newState == states.Queued {
			whyWord = "window rolled over"
		}
		*actions = append(*actions, fmt.Sprintf("%s: %s -> %s (%s)", agent, state, newState, whyWord))
		t.logf("  %s: %s -> %s (%s)", agent, state, newState, whyWord)
		state = newState
		row = must(db.GetAgentState(q, agent))
		wokeNow = wokeNow || restored
	}

	// An agent with nowhere to run cannot be given work by anyone but a human, which is what sends an agent looking for a subagent that
	// dies with its session. The one fact the daemon needs is which repository: `repo:` in agent.yaml. An answer counts as work here.
	need := t.Lifecycle.StartOrWakeNeeded(q, agent, row)
	if need != nil && need.Kind == "start" {
		said := t.Lifecycle.StartForWork(q, agent)
		*actions = append(*actions, said)
		row = must(db.GetAgentState(q, agent))
		if t.Host.SessionAlive(agent) {
			t.Delivery.NoteFailure(agent, "")
			delete(t.said, agent)
		} else {
			t.Delivery.NoteFailure(agent, "could not be started - "+said)
			// Once per reason: the tick comes round every few seconds and a refusal can last for hours.
			if t.said[agent] != said {
				t.said[agent] = said
				t.logf("  %s: %s and could not be started - %s", agent, need.What, said)
			}
		}
	}

	// A closed session cannot read its own inbox, so the arrival of work is what has to bring it back. Not one started a moment ago: that
	// terminal is a fresh session booting.
	if need != nil && need.Kind == "wake" {
		what := need.What
		ok, why := t.Lifecycle.WakeAgent(q, agent)
		if ok {
			t.Delivery.NoteFailure(agent, "")
			*actions = append(*actions, fmt.Sprintf("%s: woken for %s", agent, what))
		} else {
			t.Delivery.NoteFailure(agent, "could not be woken - "+why)
			*actions = append(*actions, fmt.Sprintf("%s: could not wake - %s", agent, why))
			// Said out loud, because this is the tick doing nothing about something that is waiting - and once, because two of these
			// reasons last for hours.
			if t.said[agent] != why {
				t.said[agent] = why
				t.logf("  %s: %s and could not be woken - %s", agent, what, why)
			}
		}
		if ok {
			delete(t.said, agent)
			row = must(db.GetAgentState(q, agent))
			wokeNow = true
		}
	}

	// Work that has arrived is delivered before anything else is decided. An agent woken in this tick is told on the next one. An agent
	// whose turn ended on the usage limit cannot run one: sending work to it would start a turn that fails at once.
	t.Delivery.TrackBackgroundWork(q, agent, row)
	row = must(db.GetAgentState(q, agent))
	atWall := t.Usage.AtLimit(q, agent)
	openIdle := state != states.Running && state != states.Handoff && !wokeNow && atWall == nil && !closedAt(row)
	if openIdle {
		// An answer first: an agent that asked something and parked is waiting on that.
		closed := t.Lifecycle.ColdBeforeSend(q, agent, row, now, st)
		if closed != nil && *closed {
			*actions = append(*actions, fmt.Sprintf("%s: cache expired; session closed, the wake delivers", agent))
			row = must(db.GetAgentState(q, agent))
		}
		if closed == nil {
			t.Delivery.DeliverAnswers(q, agent)
			// A task told past an idle head starts a turn: the nudge about that head waits.
			if !t.Delivery.DeliverQueued(q, agent) {
				t.Delivery.NudgeForgottenResolve(q, agent, row, now)
			}
		}
	}

	// An open session that has sat idle until its prompt cache is nearly spent is asked once to write its handoff note; the cost pilot
	// takes this over for an agent that has turned it on.
	if openIdle {
		if t.Cost.RenewalOn(t, agent) {
			t.Cost.Renew(t, agent, row, now, st)
		} else {
			t.Handoff.NoteAtCacheEdge(q, agent, now, st)
		}
	}

	// Pilot: an idle session over its fold size, between tasks, is closed fresh, after the note.
	if openIdle && t.Cost.FoldDue(t, agent, row, now, st) && !t.Handoff.NoteHold(q, agent, now) {
		ok, why := t.Cost.Fold(t, agent, now, st)
		if ok {
			*actions = append(*actions, fmt.Sprintf("%s: folded while idle", agent))
			row = must(db.GetAgentState(q, agent))
		} else if t.saidClose[agent] != why {
			t.saidClose[agent] = why
			t.logf("  %s: idle over its fold size but not folded - %s", agent, why)
		}
	}

	// Memory is the scarce resource on one laptop running a team, and an idle session spends it while earning nothing. Closing it clears
	// nothing, and waking it resumes the same session.
	if atWall == nil && t.Lifecycle.ShouldClose(q, agent, state, row, now, st) && !t.Handoff.NoteHold(q, agent, now) {
		ok, why := t.Lifecycle.CloseAgent(q, agent, st)
		if ok {
			*actions = append(*actions, fmt.Sprintf("%s: session closed while idle", agent))
			row = must(db.GetAgentState(q, agent))
		} else if t.saidClose[agent] != why {
			// Once per reason: a refusal that leaves no close_failed behind comes round every tick.
			t.saidClose[agent] = why
			t.logf("  %s: idle but not closed - %s", agent, why)
		}
	}

	t.Usage.Settle(q, agent)

	// A short burn-rate history per account: how fast the account is filling, which the pick weighs and the screens show. Only a figure
	// this payload actually reported may enter it, stamped with when it was observed: a carried figure pairs an unchanging percentage
	// with a collected_at that moves every tick, and the measured rate falls to zero for an account that was climbing.
	hist := samples[acct]
	if st.IsLiveReading {
		if len(hist) == 0 || hist[len(hist)-1].At != st.ShortAt {
			s := Sample{At: st.ShortAt}
			if st.ShortPct != nil {
				s.Pct, s.Known = *st.ShortPct, true
			}
			hist = append(hist, s)
			if len(hist) > BurnWindow {
				hist = hist[len(hist)-BurnWindow:]
			}
		}
	}
	samples[acct] = hist
	burn := BurnRatePerS(hist)
	// Stamped with the newest sample's time, so a pace nobody is keeping ages out.
	if len(hist) > 0 {
		t.Usage.WriteBurn(acct, burn, hist[len(hist)-1].At)
	}

	// Un-parks a usage park - PARKED only, and only when the handoff that put it there named a usage trigger - the moment that same
	// reading, evaluated the way the park itself was, no longer fires. Never on an assumed or unknown account: a guess is not evidence
	// enough to wake this agent into.
	if !wokeNow && !closedAt(row) && state == states.Parked &&
		reading != nil && reading.Source != "assumed" &&
		t.Host.SessionAlive(agent) {
		last := must(db.LastHandoffTrigger(q, agent))
		verdict := Evaluate(st.ShortTrusted, st.TrustedCtxUsed, st.LongTrusted, ctxLimit)
		movable := last != "" && t.Handoff.Movable(last)
		// Still at the wall here, but another account may have rolled over or freed a slot since the park: if the work is worth a cold
		// start, move now rather than wait out this account's own window.
		if movable && verdict.Fired && t.Handoff.Movable(verdict.Kind) {
			if dest, _ := t.Handoff.MovePlan(q, agent, verdict.Kind); dest != "" {
				moved := t.Handoff.MoveToRoom(q, agent, verdict)
				if moved {
					*actions = append(*actions, fmt.Sprintf("%s: moved off a parked account", agent))
					return
				}
			}
		}
		if movable && !verdict.Fired {
			newState := must(QueuedOrIdle(q, agent, "usage recovered"))
			*actions = append(*actions, fmt.Sprintf("%s: %s -> %s (usage recovered)", agent, state, newState))
			t.logf("  %s: %s -> %s (usage recovered)", agent, state, newState)
			row = must(db.GetAgentState(q, agent))
			state = newState
			wokeNow = true
		}
	}

	if state == states.Parked || state == states.Throttled || state == states.Degraded {
		return // waiting on wake_at, already handled above
	}

	// What sits at the other end of a closed agent is a shell prompt, and a handoff document is full of backticks. An agent woken in
	// this tick is judged on the next one: st was read before the wake and a closed agent's snapshot is stale by construction.
	if wokeNow || !t.Lifecycle.TriggersApply(row) {
		return
	}

	// trusted_* is nil only when we genuinely do not know. Unknown is not a wall, so it does not fire; a quiet window reads as 0.
	trig := Evaluate(st.ShortTrusted, st.TrustedCtxUsed, st.LongTrusted, ctxLimit)
	// A turn that ended on the usage limit means the agent is at the wall whatever the reading says (it can lag, or be the other
	// window): move it the same way a trigger would.
	if atWall != nil && !(trig.Fired && t.Handoff.Movable(trig.Kind)) {
		kind := "usage"
		if strings.Contains(strings.ToLower(atWall.Text), "week") {
			kind = "usage_7d"
		}
		src := atWall.Source
		if src == "" {
			src = "?"
		}
		trig = Trigger{true, kind, fmt.Sprintf("turn ended on its usage limit: %s (read from %s)", atWall.Text, src)}
	}
	if trig.Fired {
		// A daemon that cannot perform a handoff says so once, not as a degraded handoff on every tick: nothing was tried.
		if ns, ok := t.Handoff.(interface{ NotServed() string }); ok {
			if why := ns.NotServed(); why != "" {
				if t.saidClose[agent+"/handoff"] != why {
					t.saidClose[agent+"/handoff"] = why
					t.logf("  %s: a handoff is due (%s) but %s", agent, trig.Kind, why)
				}
				return
			}
		}
		ok := t.Handoff.PerformHandoff(q, agent, trig, "")
		suffix := ""
		if !ok {
			suffix = " -> degraded"
		}
		*actions = append(*actions, fmt.Sprintf("%s: handoff (%s)%s", agent, trig.Kind, suffix))

		fresh := must(db.GetAgentState(q, agent))
		switch {
		case fresh != nil && fresh.ThrottleCount >= states.MaxThrottleStreak:
			// 8.6: raise to the founder only after repeated automatic recovery failed.
			check(db.SetAgentState(q, agent, states.Stalled, 0,
				fmt.Sprintf("%d consecutive degraded handoffs", fresh.ThrottleCount)))
			*actions = append(*actions, fmt.Sprintf("%s: STALLED after %d degraded handoffs", agent, fresh.ThrottleCount))
			t.logf("  %s: STALLED - needs a human", agent)
		case fresh != nil && fresh.FailedAttempts >= states.MaxFailedAttempts:
			check(db.SetAgentState(q, agent, states.Stalled, 0, fmt.Sprintf("%d failed attempts", fresh.FailedAttempts)))
			*actions = append(*actions, fmt.Sprintf("%s: STALLED after %d failed attempts", agent, fresh.FailedAttempts))
		}
	}
}

// StartWaiting is, at daemon start, bringing up at once every agent that has work waiting and no session: the tick would, but only after
// its usage polls and each agent in turn, and a reboot leaves every agent in that state. It runs the sweep first, so a terminal the reboot
// killed is dropped and the agent started in its worktree.
func (t *Tick) StartWaiting() []string {
	t.init()
	var actions []string
	t.Reconcile.Sweep(t.Q, &actions)
	for _, agent := range t.Host.Agents() {
		err := guard(func() {
			row := must(db.GetAgentState(t.Q, agent))
			need := t.Lifecycle.StartOrWakeNeeded(t.Q, agent, row)
			switch {
			case need != nil && need.Kind == "start":
				actions = append(actions, t.Lifecycle.StartForWork(t.Q, agent))
			case need != nil:
				if ok, why := t.Lifecycle.WakeAgent(t.Q, agent); ok {
					actions = append(actions, agent+": woken at start")
				} else {
					actions = append(actions, fmt.Sprintf("%s: could not wake - %s", agent, why))
				}
			}
		})
		if err != nil { // one agent's failure is not the start's
			t.logf("  %s: start for waiting work failed: %v", agent, err)
		}
	}
	return actions
}

// guarded runs one of the tick's passes: its failure is logged under its name and is not the tick's failure.
func (t *Tick) guarded(name string, f func() error) {
	var err error
	if p := guard(func() { err = f() }); p != nil {
		err = p
	}
	if err != nil {
		t.logf("  %s: %v", name, err)
	}
}

// Run is one tick over the samples the caller keeps across ticks.
func (t *Tick) Run(samples map[string][]Sample) (Result, error) {
	t.init()
	now := t.Now()
	var actions []string
	if samples == nil {
		samples = map[string][]Sample{}
	}
	q := t.Q

	// Before anything is decided on what we believe, check what is true.
	t.Reconcile.Sweep(q, &actions)

	// And what is true includes the two readings every decision below is made on. Both are pulls (D11), so they happen here.
	if err := t.Sweeps.CollectSnapshot(q); err != nil {
		return Result{now, actions}, fmt.Errorf("collect snapshots: %w", err)
	}
	if err := t.Sweeps.PollUsage(q); err != nil { // a failure outside any one account is not the tick's failure either
		t.logf("  usage: poll failed: %v", err)
	}

	// Link a file added to shared/.claude into accounts registered before it.
	if t.Sweeps.RelinkAccounts != nil {
		if relinked, err := t.Sweeps.RelinkAccounts(); err != nil {
			t.logf("  relink: %v", err)
		} else {
			for _, acct := range sortedKeys(relinked) {
				t.logf("  relinked %s: %s", acct, strings.Join(relinked[acct], ", "))
			}
		}
	}

	// A handoff past its deadline must fail here, not only at startup.
	actions = append(actions, t.Reconcile.ExpireHandoffs(q)...)

	// Midnight rollover (section 5). Without it yesterday's "in progress" stays in progress forever.
	day := t.Sweeps.Today()
	if t.rolloverDay != "" && t.rolloverDay != day {
		for _, agent := range t.Host.Agents() {
			if ok, msg := t.Sweeps.Rollover(agent); ok && !strings.Contains(msg, "already exists") {
				actions = append(actions, msg)
				t.logf("  rollover: %s", msg)
			}
		}
	}
	t.rolloverDay = day

	// The human is no agent, so no per-agent delivery checks the edges of what agents wrote to them.
	t.guarded(db.Human, func() error {
		if err := t.Delivery.EnforceEdges(q, db.Human); err != nil {
			return fmt.Errorf("edge check failed: %w", err)
		}
		return nil
	})

	for _, agent := range t.Host.Agents() {
		if err := guard(func() { t.Supervise(agent, now, samples, &actions) }); err != nil {
			// A real failure, not a resource shortage: bad config, unreadable status, a host call that failed. Catching it per agent is
			// the point - supervising one agent is not allowed to end supervision of every agent after it in the list.
			t.Reconcile.StallAgent(q, agent, &actions, err, "error")
		}
	}

	t.Sweeps.BlockedSweep(q, now)
	for _, s := range t.Passes {
		s := s
		t.guarded(s.Name, func() error { return s.Run(q, now) })
	}
	t.observeBurn()
	return Result{now, actions}, nil
}

// observeBurn feeds each account's reading and who is working on it to the estimate of what one more agent costs, and logs it for a
// replay nobody has thought to ask for yet. After the agents, so it sees the states this tick left.
func (t *Tick) observeBurn() {
	q := t.Q
	working := t.Observe.ActiveByAccount(q)
	firsts := t.Observe.FirstTurnAgents(q)
	accounts := t.Host.Accounts()
	for _, acct := range accounts {
		st := t.Observe.ReadAccount(acct)
		if err := t.Observe.ObserveBurn(q, acct, working[acct], st, firsts); err != nil {
			t.logf("  burn: %s: %v", acct, err)
		}
		if err := t.Observe.RecordUsageSample(q, acct, st); err != nil {
			t.logf("  sample: %s: %v", acct, err)
		}
	}
	if err := t.Observe.PruneUsageSamples(q); err != nil {
		t.logf("  sample: prune failed: %v", err)
	}
	for _, acct := range accounts {
		if err := t.Observe.IngestTokens(q, acct); err != nil {
			t.logf("  tokens: %s: %v", acct, err)
		}
	}
	if err := t.Observe.PruneTokens(q); err != nil {
		t.logf("  tokens: prune failed: %v", err)
	}
	if err := t.Observe.RefreshLongRatios(q); err != nil {
		t.logf("  sample: week ratio failed: %v", err)
	}
}

func sortedKeys(m map[string][]string) []string {
	var out []string
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
