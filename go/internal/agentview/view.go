// Package agentview writes the stored view of each structured agent (agent_view), which agents.list and agent.get serve. A terminal
// agent's row is never touched. docs/design/agentview-structured.md has the field-by-field table and what was dropped.
package agentview

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/prompts"
	"github.com/handline/handline/go/internal/session"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/textsafe"
	"github.com/handline/handline/go/internal/timefmt"
)

// DetailCap bounds the text drawn beside a state word (the contract's state_detail).
const DetailCap = 40

// needsTextCap bounds the line needs_you says the decision is.
const needsTextCap = 120

// Sessions is the structured sessions the daemon owns.
type Sessions interface {
	Structured(agent string) bool
	Alive(agent string) bool
	// Busy: a turn is running or waits on a permission answer.
	Busy(agent string) bool
}

// Background is what the supervisor knows of an agent's background work and why typing at it would only spend quota.
type Background interface {
	HasBackgroundWork(q db.Q, agent string) (bool, error)
	BgSince(q db.Q, agent string) (int64, bool, error)
	BgStale(q db.Q, agent string, now int64) (bool, error)
	BgPromptedAt(q db.Q, agent string) (int64, error)
	WallReason(q db.Q, agent string, row *db.AgentState) (string, error)
}

// Wakes is the lifecycle's judgement of a closed agent.
type Wakes interface {
	WouldWake(row *db.AgentState) (bool, error)
	RestartRetryNote(agent string, row *db.AgentState) (string, error)
}

// Prompts is the permission prompts an agent has open.
type Prompts interface {
	OpenBlockOf(agent string) (*prompts.Block, error)
}

// NeedsYou is a decision only a person at the agent's terminal can make, as the spec's needs_you says it: what kind, since when, one line
// of what it is, and which signal told (hook or session). It comes from the hooks and the session's own file, never from the screen.
type NeedsYou struct {
	Kind, Source, Text string
	Since              int64
}

// Env is everything a view is made from. The function fields are the facts that live outside the database.
type Env struct {
	Q    db.Q
	Home string
	Sessions
	Background
	Wakes
	Prompts
	// NeedsYou is the decision an agent waits on, nil for none. Nil is no source: no agent needs anyone.
	NeedsYou  func(agent string) *NeedsYou
	Agents    func() []string
	Config    func(agent string) (model, effort string, ok bool)
	Account   func(agent string) (string, error)
	Parent    func(agent string) string
	CacheLeft func(agent string, now float64) (left float64, ok bool, err error)
	CtxPct    func(agent, account string) *int // nil: no trusted reading
	// PaneRuns is whether the terminal pane agent's row names is up with something other than its shell in front: the launch typed into a
	// pane's shell, so a claude that died leaves the pane up with the shell. Nil is never, so nothing reads as starting.
	PaneRuns func(agent string) bool
	Now      func() int64
	Log      func(string)
	// Registered is every registered agent, structured or not: an agent is gone when it is not in it. Nil is Agents.
	Registered func() []string
	// DropGone has Refresh delete the row of any agent that is gone. Off while the Python daemon still keeps the rows of terminal
	// agents: then the rows this Env does not write are none of its business, gone or not.
	DropGone bool
	// Walled is what holds back a wake of agent that is due, nil when nothing does or nothing would wake it. Nil is never.
	Walled func(agent string, row *db.AgentState) *Wall
	// LoadHeld is why the machine's load holds the start of agent's waiting work, "" when it does not. Nil is never.
	LoadHeld func(agent string) string
	// Doing is the running turn's newest tool call (its line and when it began), ok false for none. Nil is none.
	Doing func(agent string) (text string, at int64, ok bool)
}

func (e *Env) now() int64 {
	if e.Now != nil {
		return e.Now()
	}
	return time.Now().Unix()
}

func (e *Env) logf(msg string) {
	if e.Log != nil {
		e.Log(msg)
	}
}

func stamp(epoch int64) any {
	return time.Unix(epoch, 0).UTC().Format("2006-01-02T15:04:05Z")
}

func clip(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[:n])
	}
	return s
}

// roleLine is the first line of the agent's ROLE.md that is not blank and not a heading, as one line.
func (e *Env) roleLine(name string) string {
	b, err := os.ReadFile(filepath.Join(paths.AgentDir(e.Home, name), "ROLE.md"))
	if err != nil {
		return ""
	}
	for _, ln := range strings.Split(string(b), "\n") {
		if t := strings.TrimSpace(ln); t != "" && !strings.HasPrefix(strings.TrimLeft(ln, " \t"), "#") {
			return clip(textsafe.Oneline(t), 160)
		}
	}
	return ""
}

// promptHeadline is what an open prompt is, in one line.
func promptHeadline(b *prompts.Block) string {
	if b.Kind == "permission" {
		tool, _ := b.Detail["tool"].(string)
		if tool == "" {
			tool = "tool"
		}
		cmd, _ := b.Detail["command"].(string)
		return strings.TrimSpace(tool + ": " + cmd)
	}
	if b.Title != "" {
		return b.Title
	}
	return b.Kind
}

// must panics with err, which Compute turns back into its returned error.
func must[T any](v T, err error) T {
	if err != nil {
		panic(err)
	}
	return v
}

func must2[A, B any](a A, b B, err error) (A, B) {
	if err != nil {
		panic(err)
	}
	return a, b
}

func (e *Env) backgroundDetail(agent string, now int64) string {
	since, has := must2(e.BgSince(e.Q, agent))
	out := "background work"
	if has && since != 0 {
		out = "background " + timefmt.Ago(now-since)
	}
	if must(e.BgStale(e.Q, agent, now)) {
		out += ", stale"
	}
	if must(e.BgPromptedAt(e.Q, agent)) != 0 {
		out += ", told to finish or stop it"
	}
	return out
}

func (e *Env) needs(agent string) *NeedsYou {
	if e.NeedsYou == nil {
		return nil
	}
	return e.NeedsYou(agent)
}

// word is the state word the screens draw (RUNNING, IDLE, PENDING, MOVING, DIALOG, FATAL) and its detail.
func (e *Env) word(agent string, row *db.AgentState, now int64, needs *NeedsYou) (string, string) {
	if row == nil {
		return "IDLE", ""
	}
	if b := must(e.OpenBlockOf(agent)); b != nil {
		return "DIALOG", clip(textsafe.Oneline(promptHeadline(b)), DetailCap)
	}
	if needs != nil {
		return "DIALOG", clip(textsafe.Oneline(needs.Text), DetailCap)
	}
	state, detail := row.State, row.Detail.String
	if state == states.Stalled {
		return "FATAL", clip(textsafe.Oneline(detail), DetailCap)
	}
	if down := must(e.RestartRetryNote(agent, row)); down != "" {
		return "PENDING", clip(textsafe.Oneline(down), DetailCap)
	}
	switch state {
	case states.Running, states.Handoff:
		return "RUNNING", ""
	case states.Queued:
		if strings.HasPrefix(detail, states.MovingMark) {
			return "MOVING", ""
		}
		if e.Busy(agent) && e.Alive(agent) {
			return "RUNNING", ""
		}
		if must(e.HasBackgroundWork(e.Q, agent)) {
			return "IDLE", e.backgroundDetail(agent, now)
		}
		return "PENDING", ""
	case states.Parked, states.Degraded, states.Throttled:
		if strings.HasPrefix(detail, states.MovingMark) {
			return "MOVING", ""
		}
		if e.Alive(agent) && e.Busy(agent) { // a message woke it: the turn runs whatever the parked row says
			return "RUNNING", ""
		}
		if !row.WakeAt.Valid || row.WakeAt.Int64 == 0 {
			return "PENDING", ""
		}
		if state == states.Parked && !strings.HasPrefix(detail, states.ByHandMark) && must(db.UnreachedCount(e.Q, agent)) == 0 {
			return "IDLE", ""
		}
		if row.WakeAt.Int64 <= now {
			return "PENDING", "due"
		}
		return "PENDING", timefmt.Stamp(row.WakeAt.Int64, timefmt.Clock)
	}
	closed := row.ClosedAt.Valid && row.ClosedAt.Int64 != 0
	if state == states.Idle && !closed && e.Alive(agent) && e.Busy(agent) {
		return "RUNNING", ""
	}
	if state == states.Idle && closed && must(e.WouldWake(row)) { // work waits for it: it is not off, its start is pending
		return "PENDING", ""
	}
	if state == states.Idle && must(e.HasBackgroundWork(e.Q, agent)) {
		return "IDLE", e.backgroundDetail(agent, now)
	}
	return "IDLE", ""
}

// wire is the spec's lower-case word for a screen word. A PENDING row is parked when a wake time stands in the way and queued when
// only the next tick does; an idle row with no live session is no_terminal, the spec's word for nothing to show.
func (e *Env) wire(agent string, row *db.AgentState, word, detail string) (string, string) {
	switch word {
	case "RUNNING":
		return "running", detail
	case "DIALOG":
		return "waiting", detail
	case "FATAL":
		return "stalled", detail
	case "MOVING":
		return "parked", states.MovingMark
	case "PENDING":
		timed := row != nil && (row.State == states.Parked || row.State == states.Degraded || row.State == states.Throttled) &&
			row.WakeAt.Valid && row.WakeAt.Int64 != 0 && must(e.RestartRetryNote(agent, row)) == ""
		if timed {
			return "parked", detail
		}
		return "queued", detail
	}
	if row == nil || !e.Alive(agent) {
		return "no_terminal", detail
	}
	return "idle", detail
}

// launchOver are the events that say a launch is no longer starting: a hook of its session (it is up), its end, or the wake that failed.
var launchOver = []string{"SessionStart", "UserPromptSubmit", "Stop", "StopFailure", "PostCompact", "SessionEnd", "wake_failed"}

// LaunchShellGraceS is how long a launch the daemon began reads as starting while its pane still shows a shell: the launch line runs at
// that shell, through hl's bash wrapper, and under load it can take many seconds to become claude.
const LaunchShellGraceS = 60

// starting is whether a launch of agent is in progress, from what is recorded: the daemon began it (a launching event) or hl launch kept
// the start (session_starts) just before it became claude, and nothing of launchOver has come since. The pane must still run something
// other than its shell, as a launch that died is back at the shell, except within LaunchShellGraceS of the daemon's own launching event.
func (e *Env) starting(agent string) bool {
	if e.PaneRuns == nil {
		return false
	}
	kept, ok := must2(db.LastSessionStartTS(e.Q, agent))
	begun, began := must2(db.LastEventTS(e.Q, agent, "launching"))
	if !ok && !began {
		return false
	}
	if last, over := must2(db.LastEventOfTS(e.Q, agent, launchOver)); over && last >= max(kept, begun) {
		return false
	}
	return (began && begun >= kept && e.now()-begun < LaunchShellGraceS) || e.PaneRuns(agent)
}

// Compute is the view of one agent.
func (e *Env) Compute(agent string) (view map[string]any, err error) {
	defer func() {
		if r := recover(); r != nil {
			re, ok := r.(error)
			if !ok {
				panic(r)
			}
			view, err = nil, re
		}
	}()
	now := e.now()
	row := must(db.GetAgentState(e.Q, agent))
	var needs *NeedsYou // read once, so the word and needs_you agree; an agent with no row waits for nobody
	if row != nil {
		needs = e.needs(agent)
	}
	word, detail := e.word(agent, row, now, needs)
	if word == "IDLE" && detail == "" && row != nil {
		wait := must(db.ChildWait(e.Q, agent))
		if wait == "" {
			wait = must(db.BlockWait(e.Q, agent))
		}
		detail = clip(textsafe.Oneline(wait), DetailCap)
	}
	state, detail := e.wire(agent, row, word, detail)
	handoff := must(e.handoffView(agent, row, now))
	// A restart parks the agent only until its next session begins: it waits for no time, so it is queued, and starting once launched.
	if h, ok := handoff.(map[string]any); ok && state == "parked" && h["phase"] == "starting" && h["cause"] != "move" {
		state, detail = "queued", ""
	}
	if (state == "no_terminal" || state == "idle" || state == "queued") && e.starting(agent) {
		state, detail = "starting", ""
	}

	var model, effort any
	if e.Config != nil {
		if m, f, ok := e.Config(agent); ok {
			model, effort = m, f
		}
	}
	var block, blocker any
	if b := must(db.BlockGet(e.Q, agent)); b != nil && now >= b.ShowAt {
		block = map[string]any{"kind": b.Kind, "since": stamp(b.Since)}
		blocker = clip(textsafe.Oneline(session.Headline(b.Kind, b.Detail)), DetailCap)
	}
	var since any
	if row != nil {
		since = stamp(row.Updated)
	}
	var wall any
	if w := must(e.WallReason(e.Q, agent, row)); w != "" {
		wall = w
	}
	var bg any
	if at, has := must2(e.BgSince(e.Q, agent)); has {
		bg = stamp(at)
	}
	var cache any
	warm := false
	if e.CacheLeft != nil {
		if left, ok, err := e.CacheLeft(agent, float64(now)); err != nil {
			return nil, err
		} else if ok {
			cache, warm = stamp(now+int64(left)), left > 0
		}
	}
	var task any
	if tid, ok := must2(db.CurrentTask(e.Q, agent)); ok {
		if t := must(db.GetTask(e.Q, tid)); t != nil {
			task = map[string]any{"id": t.ID, "title": t.Title}
		}
	}
	var account, ctx any
	if e.Account != nil {
		if a := must(e.Account(agent)); a != "" {
			// At rest past its cache's life it is on no account: its next wake goes wherever has the most headroom.
			if warm || state == "running" || state == "starting" {
				account = a
			}
			if e.CtxPct != nil {
				if p := e.CtxPct(agent, a); p != nil {
					ctx = *p
				}
			}
		}
	}
	var doing any
	if e.Doing != nil && state == "running" {
		if text, at, ok := e.Doing(agent); ok {
			doing = map[string]any{"text": text, "at": stamp(at)}
		}
	}
	var parent any
	if e.Parent != nil {
		if p := e.Parent(agent); p != "" {
			parent = p
		}
	}
	return map[string]any{
		"name": agent, "role": e.roleLine(agent), "model": model, "effort": effort,
		"state": state, "state_detail": detail, "state_since": since,
		"block": block, "wall_reason": wall, "background_since": bg,
		"would_wake": row != nil && must(e.WouldWake(row)),
		"blocker":    blocker, "account": account, "task": task, "cache_until": cache, "ctx_pct": ctx,
		"has_dialog": word == "DIALOG", "parent": parent, "needs_you": needsView(needs), "handoff": handoff, "doing": doing,
		"status": e.status(agent, row, state, detail, handoff, now).Map(),
	}, nil
}

func needsView(n *NeedsYou) any {
	if n == nil {
		return nil
	}
	var text any
	if t := clip(textsafe.Oneline(n.Text), needsTextCap); t != "" {
		text = t
	}
	return map[string]any{"kind": n.Kind, "since": stamp(n.Since), "text": text, "source": n.Source}
}

const upsert = "INSERT INTO agent_view(agent, view, updated) VALUES(?, ?, ?) " +
	"ON CONFLICT(agent) DO UPDATE SET view = excluded.view, updated = excluded.updated WHERE view != excluded.view"

// RefreshOne stores a fresh view for one structured agent now. A terminal agent's row is left alone, and a view that cannot be worked
// out keeps the last one.
func (e *Env) RefreshOne(agent string) {
	if !e.Structured(agent) {
		return
	}
	view, err := e.Compute(agent)
	if err != nil {
		e.logf("  agent view: " + agent + ": " + err.Error())
		return
	}
	b, _ := json.Marshal(view)
	if _, err := e.Q.Exec(upsert, agent, string(b), e.now()); err != nil {
		e.logf("  agent view: " + agent + ": " + err.Error())
	}
}

// Refresh stores a fresh view for every structured agent, each failing alone, and (DropGone) drops the row of an agent that is gone.
func (e *Env) Refresh() error {
	names := e.Agents()
	known := make(map[string]bool, len(names))
	for _, n := range names {
		known[n] = true
		e.RefreshOne(n)
	}
	if !e.DropGone {
		return nil
	}
	if e.Registered != nil {
		for _, n := range e.Registered() {
			known[n] = true // a terminal agent's row is not written here, and it is not gone
		}
	}
	rows, err := e.Q.Query("SELECT agent FROM agent_view")
	if err != nil {
		return err
	}
	var gone []string
	for rows.Next() {
		var n string
		if err := rows.Scan(&n); err != nil {
			rows.Close()
			return err
		}
		if !known[n] {
			gone = append(gone, n)
		}
	}
	rows.Close()
	for _, n := range gone {
		if _, err := e.Q.Exec("DELETE FROM agent_view WHERE agent = ?", n); err != nil {
			return err
		}
	}
	return nil
}

// handoffView is the handoff the agent is in the middle of, nil for none: its note is awaited or its record taken (writing), or the
// session is closed for it and the next not yet shown as a New session line, which needs its session id (starting). A handoff that
// parks the agent until a usage window resets is not starting. The cause is session.cause's word.
func (e *Env) handoffView(agent string, row *db.AgentState, now int64) (any, error) {
	h, err := db.LatestHandoff(e.Q, agent)
	if err != nil {
		return nil, err
	}
	view := func(kind, phase string, since int64) any {
		switch kind {
		case "context":
			kind = "context_full"
		case "forced":
			kind = "move"
		}
		return map[string]any{"cause": kind, "phase": phase, "since": stamp(since)}
	}
	if h != nil && h.State == "in_flight" {
		since := h.Updated
		if h.Deadline.Valid {
			since = h.Deadline.Int64 - states.HandoffDeadlineS
		}
		return view(h.TriggerKind.String, "writing", since), nil
	}
	if kind, since, ok, err := db.NoteWaitKept(e.Q, agent, now); err != nil {
		return nil, err
	} else if ok {
		return view(kind, "writing", since), nil
	}
	if h == nil || row == nil || (row.State != states.Parked && row.ClosedAt.Int64 == 0) || now-h.Updated > db.NoteWaitStaleS {
		return nil, nil
	}
	if k := h.TriggerKind.String; k == "usage" || k == "usage_7d" || k == states.ByHandMark {
		return nil, nil
	}
	last, err := db.LastBoundSessionStart(e.Q, agent)
	if err != nil || last >= h.Updated {
		return nil, err
	}
	return view(h.TriggerKind.String, "starting", h.Updated), nil
}
