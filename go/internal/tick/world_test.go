package tick

import (
	"fmt"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
)

// world scripts every port the tick reaches and keeps what it was asked, in order. The fakes embed their port interface and override what
// the cases touch, so a port a case should not reach panics on the nil.
type world struct {
	t     *testing.T
	calls []string
	logs  []string

	agents      []string
	alive       map[string]bool
	idle        bool
	reading     *AccountReading
	running     string
	status      Status
	ctxLimit    int
	atLimit     *Limit
	need        *Need
	wakeOK      bool
	wakeWhy     string
	startSaid   string
	startAlive  bool // a start leaves a session running
	waiting     bool
	resumable   bool
	shouldClose bool
	hold        bool
	closeOK     bool
	closeWhy    string
	cold        *bool
	triggers    bool // TriggersApply
	movable     map[string]bool
	movePlan    string
	moved       bool
	performOK   bool
	bgWork      bool
	idleSince   int64
	idleHave    bool
	delivered   bool
	cfg         map[string]any
	noteEnabled bool
	asked       bool
	askedTurns  int
	askOK       bool
	answered    bool
	noteText    string
	cache       *CacheClock
	restartOK   bool
	restartSaid string
	present     bool
}

func (w *world) call(format string, a ...any) { w.calls = append(w.calls, fmt.Sprintf(format, a...)) }
func (w *world) did(prefix string) bool {
	for _, c := range w.calls {
		if strings.HasPrefix(c, prefix) {
			return true
		}
	}
	return false
}

type wHost struct {
	Host
	w *world
}

func (h wHost) Agents() []string             { return h.w.agents }
func (h wHost) Accounts() []string           { return nil }
func (h wHost) SessionAlive(a string) bool   { return h.w.alive[a] }
func (h wHost) SessionIdle(string) bool      { return h.w.idle }
func (h wHost) RunningAccount(string) string { return h.w.running }

type wUsage struct {
	Usage
	w *world
}

func (u wUsage) AccountReading(db.Q, string) (AccountReading, bool) {
	if u.w.reading == nil {
		return AccountReading{}, false
	}
	return *u.w.reading, true
}
func (u wUsage) ReadForAgent(string, string) Status         { return u.w.status }
func (u wUsage) ContextLimit(string, string) int            { return u.w.ctxLimit }
func (u wUsage) AtLimit(db.Q, string) *Limit                { return u.w.atLimit }
func (u wUsage) Settle(db.Q, string)                        { u.w.call("settle") }
func (u wUsage) WriteBurn(acct string, r float64, at int64) { u.w.call("burn %s", acct) }

type wDelivery struct {
	Delivery
	w *world
}

func (d wDelivery) EnforceEdges(db.Q, string) error                           { d.w.call("edges"); return nil }
func (d wDelivery) NoteFailure(a, why string)                                 { d.w.call("failure %q", why) }
func (d wDelivery) TrackBackgroundWork(db.Q, string, *db.AgentState)          {}
func (d wDelivery) DeliverAnswers(db.Q, string)                               { d.w.call("answers") }
func (d wDelivery) DeliverQueued(db.Q, string) bool                           { d.w.call("queued"); return d.w.delivered }
func (d wDelivery) NudgeForgottenResolve(db.Q, string, *db.AgentState, int64) { d.w.call("nudge") }
func (d wDelivery) SessionPresent(string) bool                                { return d.w.present }
func (d wDelivery) HasBackgroundWork(db.Q, string) bool                       { return d.w.bgWork }
func (d wDelivery) IdleSince(db.Q, string) (int64, bool)                      { return d.w.idleSince, d.w.idleHave }

type wMessaging struct {
	Messaging
	w *world
}

func (m wMessaging) LandingSweep(db.Q, string)    {}
func (m wMessaging) TellUndelivered(db.Q, string) {}
func (m wMessaging) DeliverPending(db.Q, string)  {}

type wLifecycle struct {
	Lifecycle
	w *world
}

func (l wLifecycle) WaitingFor(db.Q, string) bool                 { return l.w.waiting }
func (l wLifecycle) ResumableWithoutTerminal(*db.AgentState) bool { return l.w.resumable }
func (l wLifecycle) WakeAgent(_ db.Q, a string) (bool, string) {
	l.w.call("wake %s", a)
	return l.w.wakeOK, l.w.wakeWhy
}
func (l wLifecycle) StartOrWakeNeeded(db.Q, string, *db.AgentState) *Need { return l.w.need }
func (l wLifecycle) StartForWork(_ db.Q, a string) string {
	l.w.call("start %s", a)
	if l.w.startAlive {
		l.w.alive[a] = true
	}
	return l.w.startSaid
}
func (l wLifecycle) ColdBeforeSend(db.Q, string, *db.AgentState, int64, Status) *bool {
	return l.w.cold
}
func (l wLifecycle) ShouldClose(db.Q, string, string, *db.AgentState, int64, Status) bool {
	return l.w.shouldClose
}
func (l wLifecycle) CloseAgent(_ db.Q, a string, _ Status) (bool, string) {
	l.w.call("close %s", a)
	return l.w.closeOK, l.w.closeWhy
}
func (l wLifecycle) CloseFresh(_ db.Q, a string, _ Status) (bool, string) {
	l.w.call("closefresh %s", a)
	return l.w.closeOK, l.w.closeWhy
}
func (l wLifecycle) RestartAgent(_ db.Q, a string, _ Status, by string, applying []string, cause string) (bool, string) {
	l.w.call("restart %s by %s for %s (%s)", a, by, strings.Join(applying, ","), cause)
	return l.w.restartOK, l.w.restartSaid
}
func (l wLifecycle) TriggersApply(*db.AgentState) bool { return l.w.triggers }

type wHandoff struct {
	Handoff
	w *world
}

func (h wHandoff) SettleOwedClear(db.Q, string) bool              { return false }
func (h wHandoff) Movable(kind string) bool                       { return h.w.movable[kind] }
func (h wHandoff) MovePlan(db.Q, string, string) (string, string) { return h.w.movePlan, "" }
func (h wHandoff) MoveToRoom(db.Q, string, Trigger) bool          { h.w.call("moveroom"); return h.w.moved }
func (h wHandoff) PerformHandoff(_ db.Q, a string, t Trigger, dest string) bool {
	h.w.call("handoff %s %s", a, t.Kind)
	return h.w.performOK
}
func (h wHandoff) NoteHold(db.Q, string, int64) bool           { return h.w.hold }
func (h wHandoff) NoteAtCacheEdge(db.Q, string, int64, Status) { h.w.call("cacheedge") }
func (h wHandoff) NoteEnabled(string) bool                     { return h.w.noteEnabled }
func (h wHandoff) NoteAsked(db.Q, string) (int, bool)          { return h.w.askedTurns, h.w.asked }
func (h wHandoff) NoteAsk(_ db.Q, a, why string) bool {
	h.w.call("ask %s %s", a, why)
	return h.w.askOK
}
func (h wHandoff) NoteAnsweredTurn(db.Q, string) bool   { return h.w.answered }
func (h wHandoff) NoteText(db.Q, string) (string, bool) { return h.w.noteText, h.w.noteText != "" }
func (h wHandoff) AskMarginS() int                      { return 30 }

type wConfig struct{ w *world }

func (c wConfig) AgentConfig(string) map[string]any { return c.w.cfg }
func (c wConfig) Seconds(_ string, def int64) int64 { return def }

type wCost struct{ w *world }

func (c wCost) ReadCache(string) *CacheClock { return c.w.cache }

// newWorld is a tick over scripted ports at time 10_000 with one open agent, "a", in IDLE.
func newWorld(t *testing.T) (*Tick, *world) {
	t.Helper()
	w := &world{t: t, agents: []string{"a"}, alive: map[string]bool{"a": true}, movable: map[string]bool{}, ctxLimit: 200_000, cfg: map[string]any{},
		triggers: true, present: true}
	tk := &Tick{Q: openDB(t), Log: func(s string) { w.logs = append(w.logs, s) }, Now: func() int64 { return 10_000 },
		Host: wHost{w: w}, Usage: wUsage{w: w}, Delivery: wDelivery{w: w}, Messaging: wMessaging{w: w}, Lifecycle: wLifecycle{w: w},
		Handoff: wHandoff{w: w}, Config: wConfig{w}, Cost: &CostPilot{Ports: wCost{w}}}
	return tk, w
}

func (w *world) logged(sub string) bool {
	for _, l := range w.logs {
		if strings.Contains(l, sub) {
			return true
		}
	}
	return false
}

func has(actions []string, sub string) bool {
	for _, a := range actions {
		if strings.Contains(a, sub) {
			return true
		}
	}
	return false
}
