package agentview

import (
	"encoding/json"
	"errors"
	"path/filepath"
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
