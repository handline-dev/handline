package agentview

import (
	"encoding/json"
	"errors"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/prompts"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/store"
)

type fakeSessions struct{ structured, alive, busy map[string]bool }

func (f fakeSessions) Structured(a string) bool { return f.structured[a] }
func (f fakeSessions) Alive(a string) bool      { return f.alive[a] }
func (f fakeSessions) Busy(a string) bool       { return f.busy[a] }

type fakeBackground struct{ wallErr map[string]error }

func (fakeBackground) HasBackgroundWork(db.Q, string) (bool, error) { return false, nil }
func (fakeBackground) BgSince(db.Q, string) (int64, bool, error)    { return 0, false, nil }
func (fakeBackground) BgStale(db.Q, string, int64) (bool, error)    { return false, nil }
func (fakeBackground) BgPromptedAt(db.Q, string) (int64, error)     { return 0, nil }
func (f fakeBackground) WallReason(_ db.Q, a string, _ *db.AgentState) (string, error) {
	return "", f.wallErr[a]
}

type fakeWakes struct{}

func (fakeWakes) WouldWake(*db.AgentState) (bool, error)                  { return false, nil }
func (fakeWakes) RestartRetryNote(string, *db.AgentState) (string, error) { return "", nil }

type fakePrompts struct{ open map[string]*prompts.Block }

func (f fakePrompts) OpenBlockOf(a string) (*prompts.Block, error) { return f.open[a], nil }

type rig struct {
	env  *Env
	sess fakeSessions
	bg   fakeBackground
	pr   fakePrompts
}

func newRig(t *testing.T, structured ...string) *rig {
	t.Helper()
	d, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	r := &rig{
		sess: fakeSessions{map[string]bool{}, map[string]bool{}, map[string]bool{}},
		bg:   fakeBackground{wallErr: map[string]error{}},
		pr:   fakePrompts{open: map[string]*prompts.Block{}},
	}
	names := []string{}
	for _, n := range structured {
		r.sess.structured[n] = true
		names = append(names, n)
	}
	r.env = &Env{Q: d, Home: t.TempDir(), Sessions: r.sess, Background: r.bg, Wakes: fakeWakes{}, Prompts: r.pr,
		Agents: func() []string { return names }, Now: func() int64 { return 5000 }}
	return r
}

func (r *rig) state(t *testing.T, agent, state, detail string) {
	t.Helper()
	if err := db.SetAgentState(r.env.Q, agent, state, 0, detail); err != nil {
		t.Fatal(err)
	}
}

func (r *rig) stored(t *testing.T, agent string) map[string]any {
	t.Helper()
	var raw string
	if err := r.env.Q.QueryRow("SELECT view FROM agent_view WHERE agent = ?", agent).Scan(&raw); err != nil {
		t.Fatalf("no stored view for %s: %v", agent, err)
	}
	out := map[string]any{}
	if err := json.Unmarshal([]byte(raw), &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func TestViewOfAStructuredAgentFollowsItsSession(t *testing.T) {
	r := newRig(t, "run", "idle", "ask", "crash", "gone")
	r.state(t, "run", states.Running, "")
	r.sess.alive["run"], r.sess.busy["run"] = true, true
	r.state(t, "idle", states.Idle, "")
	r.sess.alive["idle"] = true
	r.state(t, "ask", states.Running, "")
	r.sess.alive["ask"], r.sess.busy["ask"] = true, true
	r.pr.open["ask"] = &prompts.Block{Kind: "permission", Detail: map[string]any{"tool": "Bash", "command": "rm -rf build"}}
	r.state(t, "crash", states.Stalled, "the session exited: signal: killed")
	r.state(t, "gone", states.Idle, "")

	r.env.RefreshOne("run")
	r.env.RefreshOne("idle")
	r.env.RefreshOne("ask")
	r.env.RefreshOne("crash")
	r.env.RefreshOne("gone")

	for _, c := range []struct {
		agent, state, detail string
		dialog               bool
	}{
		{"run", "running", "", false},
		{"idle", "idle", "", false},
		{"ask", "waiting", "Bash: rm -rf build", true},
		{"crash", "stalled", "the session exited: signal: killed", false},
		{"gone", "no_terminal", "", false},
	} {
		v := r.stored(t, c.agent)
		if v["state"] != c.state || v["state_detail"] != c.detail || v["has_dialog"] != c.dialog {
			t.Errorf("%s: state %v / %q, dialog %v; want %s / %q, %v", c.agent, v["state"], v["state_detail"], v["has_dialog"], c.state, c.detail, c.dialog)
		}
	}
}

func TestAnAgentThatNeedsAPersonIsWaitingWithNeedsYouAndOneThatDoesNotHasNull(t *testing.T) {
	r := newRig(t, "needy", "plain", "noline")
	for _, a := range []string{"needy", "plain", "noline"} {
		r.state(t, a, states.Idle, "")
		r.sess.alive[a] = true
	}
	r.env.NeedsYou = func(a string) *NeedsYou {
		switch a {
		case "needy":
			return &NeedsYou{Kind: "permission", Source: "hook", Text: "Bash: ls", Since: 4000}
		case "noline":
			return &NeedsYou{Kind: "dialog", Source: "session", Since: 4100}
		}
		return nil
	}
	for _, a := range []string{"needy", "plain", "noline"} {
		r.env.RefreshOne(a)
	}
	v := r.stored(t, "needy")
	n, _ := v["needs_you"].(map[string]any)
	if v["state"] != "waiting" || v["has_dialog"] != true || v["state_detail"] != "Bash: ls" || n["kind"] != "permission" || n["source"] != "hook" ||
		n["text"] != "Bash: ls" || n["since"] != "1970-01-01T01:06:40Z" {
		t.Errorf("needy: %v", v)
	}
	if n := r.stored(t, "noline")["needs_you"].(map[string]any); n["text"] != nil || n["kind"] != "dialog" {
		t.Errorf("a need with no text says null: %v", n)
	}
	if v := r.stored(t, "plain"); v["needs_you"] != nil || v["state"] != "idle" || v["has_dialog"] != false {
		t.Errorf("plain: %v", v)
	}
}

func TestNeedsYouIsReadOnceAndNeverForAnAgentWithNoRow(t *testing.T) {
	r := newRig(t, "norow", "long")
	r.state(t, "long", states.Running, "")
	reads := 0
	r.env.NeedsYou = func(a string) *NeedsYou {
		reads++
		return &NeedsYou{Kind: "dialog", Source: "session", Text: "line one\nline two " + strings.Repeat("x", 300), Since: 4000}
	}
	r.env.RefreshOne("norow")
	if v := r.stored(t, "norow"); v["needs_you"] != nil || v["has_dialog"] != false || v["state"] != "no_terminal" || reads != 0 {
		t.Errorf("an agent with no row waits for nobody: %v (read %d times)", v, reads)
	}
	r.env.RefreshOne("long")
	n := r.stored(t, "long")["needs_you"].(map[string]any)
	if reads != 1 || strings.Contains(n["text"].(string), "\n") || len([]rune(n["text"].(string))) != needsTextCap {
		t.Errorf("read %d times; text %q", reads, n["text"])
	}
}

func TestALiveTurnShowsRunningWhateverTheRowSays(t *testing.T) {
	r := newRig(t, "a")
	r.state(t, "a", states.Idle, "")
	r.sess.alive["a"], r.sess.busy["a"] = true, true
	r.env.RefreshOne("a")
	if got := r.stored(t, "a")["state"]; got != "running" {
		t.Fatalf("state %v, want running", got)
	}
}

func TestAQueuedMoveIsParkedMoving(t *testing.T) {
	r := newRig(t, "a")
	r.state(t, "a", states.Queued, states.MovingMark+" to b")
	r.env.RefreshOne("a")
	v := r.stored(t, "a")
	if v["state"] != "parked" || v["state_detail"] != "moving" {
		t.Fatalf("got %v / %v", v["state"], v["state_detail"])
	}
}

func TestATerminalAgentKeepsItsStoredView(t *testing.T) {
	r := newRig(t, "s")
	r.state(t, "t", states.Running, "")
	r.env.Agents = func() []string { return []string{"s", "t"} }
	const kept = `{"name":"t","state":"idle","from":"python"}`
	if _, err := r.env.Q.Exec("INSERT INTO agent_view(agent, view, updated) VALUES('t', ?, 1)", kept); err != nil {
		t.Fatal(err)
	}
	r.state(t, "s", states.Idle, "")
	if err := r.env.Refresh(); err != nil {
		t.Fatal(err)
	}
	var raw string
	var updated int64
	if err := r.env.Q.QueryRow("SELECT view, updated FROM agent_view WHERE agent = 't'").Scan(&raw, &updated); err != nil {
		t.Fatal(err)
	}
	if raw != kept || updated != 1 {
		t.Fatalf("terminal agent's row changed: %s @ %d", raw, updated)
	}
	r.stored(t, "s")
}

func TestARowForAnAgentThatIsGoneGoesAndAFailingAgentKeepsItsLast(t *testing.T) {
	r := newRig(t, "a", "b")
	r.state(t, "a", states.Idle, "")
	r.state(t, "b", states.Idle, "")
	if _, err := r.env.Q.Exec("INSERT INTO agent_view(agent, view, updated) VALUES('old', '{}', 1)"); err != nil {
		t.Fatal(err)
	}
	if err := r.env.Refresh(); err != nil { // DropGone off: a row not written here stays
		t.Fatal(err)
	}
	var kept int
	if err := r.env.Q.QueryRow("SELECT count(*) FROM agent_view WHERE agent = 'old'").Scan(&kept); err != nil || kept != 1 {
		t.Fatalf("a row this Env does not write was dropped (%d, %v)", kept, err)
	}
	r.env.DropGone = true
	before := r.stored(t, "b")
	r.bg.wallErr["b"] = errors.New("db is locked")
	r.state(t, "a", states.Running, "")
	r.state(t, "b", states.Running, "")
	var logs []string
	r.env.Log = func(s string) { logs = append(logs, s) }
	if err := r.env.Refresh(); err != nil {
		t.Fatal(err)
	}
	if got := r.stored(t, "b"); got["state"] != before["state"] {
		t.Errorf("b's view changed to %v although it could not be worked out", got["state"])
	}
	if got := r.stored(t, "a")["state"]; got != "running" {
		t.Errorf("a stayed %v behind b's failure", got)
	}
	if len(logs) != 1 {
		t.Errorf("logs %v", logs)
	}
	var n int
	if err := r.env.Q.QueryRow("SELECT count(*) FROM agent_view WHERE agent = 'old'").Scan(&n); err != nil || n != 0 {
		t.Errorf("gone agent's row kept (%d, %v)", n, err)
	}
}

func TestAHandoffShowsFromTheNoteWaitThroughTheLaunchUntilTheNewSessionBegins(t *testing.T) {
	r := newRig(t, "a")
	r.state(t, "a", states.Idle, "")
	handoff := func() any {
		t.Helper()
		r.env.RefreshOne("a")
		return r.stored(t, "a")["handoff"]
	}
	if h := handoff(); h != nil {
		t.Fatalf("no handoff, got %v", h)
	}

	if err := db.MarkNoteWait(r.env.Q, "a", "context", 4990); err != nil {
		t.Fatal(err)
	}
	if err := db.MarkNoteWait(r.env.Q, "a", "context", 4995); err != nil { // the wait keeps its start
		t.Fatal(err)
	}
	h, _ := handoff().(map[string]any)
	if h["cause"] != "context_full" || h["phase"] != "writing" || h["since"] != stamp(4990) {
		t.Fatalf("note wait: %v", h)
	}

	db.ClearNoteWait(r.env.Q, "a")
	if err := db.StartHandoff(r.env.Q, "h1", "a", "", "context", 5060); err != nil {
		t.Fatal(err)
	}
	h, _ = handoff().(map[string]any)
	if h["cause"] != "context_full" || h["phase"] != "writing" || h["since"] != stamp(5060-states.HandoffDeadlineS) {
		t.Fatalf("in flight: %v", h)
	}

	if err := db.AdvanceHandoff(r.env.Q, "h1", "released", "done", ""); err != nil {
		t.Fatal(err)
	}
	r.state(t, "a", states.Parked, "")
	h, _ = handoff().(map[string]any)
	if h["cause"] != "context_full" || h["phase"] != "starting" {
		t.Fatalf("released: %v", h)
	}
	// Parked until a time already past, as a restart parks it, with work waiting: it waits for its next session, not a clock, so the
	// agent list keeps it among the waking, not the sleeping.
	db.AddPending(r.env.Q, "a", "x", 1)
	if err := db.SetAgentState(r.env.Q, "a", states.Parked, 5000, ""); err != nil {
		t.Fatal(err)
	}
	r.env.RefreshOne("a")
	if v := r.stored(t, "a"); v["state"] != "queued" || v["state_detail"] != "" {
		t.Fatalf("a restart reads %v %q", v["state"], v["state_detail"])
	}

	// One restart is one state: the line stays until the chat can show the New session line, which needs the session's id.
	if _, err := db.NoteSessionStart(r.env.Q, "a", "context_full", false, "", "", "", ""); err != nil {
		t.Fatal(err)
	}
	if h := handoff(); h == nil {
		t.Fatal("a launch with no session id yet has no New session line, so the restart is still on")
	}
	if _, err := r.env.Q.Exec("UPDATE session_starts SET session_id = 's2' WHERE agent = 'a'"); err != nil {
		t.Fatal(err)
	}
	if h := handoff(); h != nil {
		t.Fatalf("the new session is shown, got %v", h)
	}
}

func TestAHandoffForAUsageWindowDoesNotShowAsStartingAndAStaleNoteWaitShowsNothing(t *testing.T) {
	r := newRig(t, "a")
	r.state(t, "a", states.Parked, "")
	db.StartHandoff(r.env.Q, "h1", "a", "", "usage", 5060)
	db.AdvanceHandoff(r.env.Q, "h1", "released", "done", "")
	r.env.RefreshOne("a")
	if h := r.stored(t, "a")["handoff"]; h != nil {
		t.Fatalf("usage: %v", h)
	}
	db.MarkNoteWait(r.env.Q, "a", "context", 5000-db.NoteWaitStaleS-1)
	r.env.RefreshOne("a")
	if h := r.stored(t, "a")["handoff"]; h != nil {
		t.Fatalf("stale: %v", h)
	}
}

func TestANoteWaitThatEndedWithItsSessionDoesNotHoldTheNextOneToItsTimeOrKind(t *testing.T) {
	r := newRig(t, "a")
	r.state(t, "a", states.Idle, "")
	db.MarkNoteWait(r.env.Q, "a", "context", 1000)
	db.MarkNoteWait(r.env.Q, "a", "usage", 4990)
	r.env.RefreshOne("a")
	h, _ := r.stored(t, "a")["handoff"].(map[string]any)
	if h["cause"] != "usage" || h["phase"] != "writing" || h["since"] != stamp(4990) {
		t.Fatalf("got %v", h)
	}
}

func TestALaunchReadsStartingFromWhatIsRecordedUntilItsSessionsFirstHook(t *testing.T) {
	r := newRig(t, "new", "busy")
	r.state(t, "new", states.Idle, "")
	r.state(t, "busy", states.Running, "")
	r.sess.alive["busy"], r.sess.busy["busy"] = true, true
	pane := map[string]bool{"new": true, "busy": true} // whether claude, not the shell, is in front of the pane
	r.env.PaneRuns = func(a string) bool { return pane[a] }
	for _, a := range []string{"new", "busy"} { // hl launch kept the start just before it became claude
		r.env.Q.Exec("INSERT INTO session_starts(agent, ts, cause, resumed, text, prompt) VALUES(?, 4990, 'wake', 0, '', '')", a)
	}
	word := func(a string) any { r.env.RefreshOne(a); return r.stored(t, a)["state"] }
	if w := word("new"); w != "starting" {
		t.Fatalf("a launch whose session has sent nothing yet: %v", w)
	}
	if w := word("busy"); w != "running" {
		t.Fatalf("starting never hides a turn that runs: %v", w)
	}
	db.AddEventAt(r.env.Q, 4980, "new", "Stop") // the session before this launch
	if w := word("new"); w != "starting" {
		t.Fatalf("an older session's hook does not end this launch: %v", w)
	}
	pane["new"] = false
	if w := word("new"); w == "starting" {
		t.Fatal("a launch whose claude died is back at the shell, or its pane is gone: not starting, however long ago it began")
	}
	pane["new"] = true
	for _, over := range []string{"wake_failed", "SessionEnd", "UserPromptSubmit"} {
		r.env.Q.Exec("DELETE FROM events WHERE ts > 4990")
		db.AddEventAt(r.env.Q, 4995, "new", over)
		if w := word("new"); w == "starting" {
			t.Fatalf("%s after the start ends starting", over)
		}
	}
}

func TestALaunchTheDaemonBeganReadsStartingBeforeHlLaunchKeepsItsStart(t *testing.T) {
	r := newRig(t, "new")
	r.state(t, "new", states.Idle, "")
	shell := true // the launch line is typed at the pane's shell and runs there through hl's bash wrapper
	r.env.PaneRuns = func(string) bool { return !shell }
	db.AddEventAt(r.env.Q, 4980, "new", "SessionEnd") // the session before this launch
	db.AddEventAt(r.env.Q, 4990, "new", "launching")
	r.env.RefreshOne("new")
	if w := r.stored(t, "new")["state"]; w != "starting" {
		t.Fatalf("no session_starts row yet, the pane still at its shell: %v", w)
	}
	now := r.env.Now
	r.env.Now = func() int64 { return 4990 + LaunchShellGraceS }
	r.env.RefreshOne("new")
	if w := r.stored(t, "new")["state"]; w == "starting" {
		t.Fatal("a launch still at the shell after the grace died there")
	}
	shell = false
	r.env.RefreshOne("new")
	if w := r.stored(t, "new")["state"]; w != "starting" {
		t.Fatalf("a launch whose pane runs claude is starting however long it takes: %v", w)
	}
	r.env.Now = now
	db.AddEventAt(r.env.Q, 4995, "new", "SessionStart")
	r.env.RefreshOne("new")
	if w := r.stored(t, "new")["state"]; w == "starting" {
		t.Fatal("its first hook ends starting")
	}
}
