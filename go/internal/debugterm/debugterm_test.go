package debugterm

import (
	"encoding/json"
	"path/filepath"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/term"
)

type host struct {
	panes []term.Pane
	ok    bool
}

func (h *host) Name() string                                   { return "tmux" }
func (h *host) Available() bool                                { return true }
func (h *host) ListTerminals() map[string]bool                 { return nil }
func (h *host) ReadScreen(string, int) *term.Screen            { return nil }
func (h *host) Send(string, string, bool) (bool, string)       { return false, "" }
func (h *host) WaitIdle(string, int, func([]string) bool) bool { return false }
func (h *host) ListPanes() ([]term.Pane, bool)                 { return h.panes, h.ok }

type plain struct{ host }

func (plain) ListPanes() {} // hides the lister: a host that cannot list panes

func rig(t *testing.T, h term.Backend, procs map[int]Proc) *Env {
	t.Helper()
	db, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })
	for _, q := range []string{
		"INSERT INTO agent_state(agent, state, updated, terminal, account, worktree, resume_session) VALUES('ann', 'IDLE', 5, '%1', 'acct.one', 'repo::/w/ann', 'old-sid')",
		"INSERT INTO session_starts(agent, ts, session_id, cause, resumed, text, prompt) VALUES('ann', 1, 'sid-1', 'wake', 0, '', '')",
		`INSERT INTO agent_view(agent, view, updated) VALUES('ann', '{"cache_until":"1970-01-01T01:00:00Z","blocker":"secret"}', 1)`,
		"INSERT INTO agent_terminals(handle, agent, opened_at) VALUES('%2', 'bob', 1)",
	} {
		if _, err := db.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
	var procs2 func() map[int]Proc
	if procs != nil {
		procs2 = func() map[int]Proc { return procs }
	}
	return &Env{DB: db, Backend: func() term.Backend { return h }, Procs: procs2, Now: func() time.Time { return time.Unix(1000, 0) }}
}

func panes(t *testing.T, e *Env) []map[string]any {
	t.Helper()
	got, err := e.List()
	if err != nil {
		t.Fatal(err)
	}
	b, _ := json.Marshal(got)
	var m struct{ Panes []map[string]any }
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatal(err)
	}
	return m.Panes
}

func TestAPaneIsDescribedByTheHostTheProcessesAndTheAgentItIs(t *testing.T) {
	h := &host{ok: true, panes: []term.Pane{
		{Session: "ocp", Window: "0", WindowName: "ann", Handle: "%1", PID: 10, Command: "2.1.0", Path: "/w/ann", Activity: 400},
		{Session: "ocp", Window: "1", WindowName: "bob", Handle: "%2", PID: 20, Command: "zsh", Path: "/w/bob", Activity: 990},
		{Session: "ocp", Window: "2", Handle: "%3", PID: 30, Command: "vim"},
	}}
	procs := map[int]Proc{10: {1, "-zsh"}, 11: {10, "/opt/bin/claude"}, 20: {1, "-zsh"}, 30: {1, "vim"}}
	ps := panes(t, rig(t, h, procs))
	if len(ps) != 3 {
		t.Fatalf("%v", ps)
	}
	ann, bob, other := ps[0], ps[1], ps[2]
	if ann["agent"] != "ann" || ann["orphan"] != false || ann["process"] != "agent" || ann["account"] != "acct.one" || ann["worktree"] != "/w/ann" ||
		ann["session_id"] != "sid-1" || ann["cache_until"] != "1970-01-01T01:00:00Z" || ann["idle_s"] != float64(600) ||
		ann["last_activity"] != "1970-01-01T00:06:40Z" || ann["pid"] != float64(10) || ann["orca_terminal"] != nil || ann["closed"] != false {
		t.Fatalf("ann: %v", ann)
	}
	if ap, _ := ann["agent_process"].(map[string]any); ap["pid"] != float64(11) || ap["name"] != "claude" {
		t.Fatalf("the agent CLI under the pane: %v", ann["agent_process"])
	}
	if _, leaked := ann["blocker"]; leaked {
		t.Fatal("only cache_until is read from the agent's view")
	}
	if bob["agent"] != nil || bob["orphan"] != true || bob["recorded_agent"] != "bob" || bob["process"] != "shell" || bob["agent_process"] != nil ||
		bob["cache_until"] != nil || bob["session_id"] != nil {
		t.Fatalf("a shell left behind: %v", bob)
	}
	if other["process"] != "other" || other["recorded_agent"] != nil || other["idle_s"] != nil || other["last_activity"] != nil {
		t.Fatalf("a pane the daemon never had: %v", other)
	}
}

func TestWithoutTheProcessTableThePaneCommandDecides(t *testing.T) {
	h := &host{ok: true, panes: []term.Pane{{Handle: "%1", Command: "claude"}, {Handle: "%4", Command: "zsh"}, {Handle: "%5", Command: "make"}}}
	ps := panes(t, rig(t, h, nil))
	if ps[0]["process"] != "agent" || ps[1]["process"] != "shell" || ps[2]["process"] != "unknown" {
		t.Fatalf("%v", ps)
	}
}

func TestAHostThatCannotListOrAnswerIsRefused(t *testing.T) {
	for _, h := range []term.Backend{&plain{}, &host{ok: false}} {
		_, err := rig(t, h, nil).List()
		if re, ok := err.(*rpc.Error); !ok || re.Code != "refused" {
			t.Fatalf("%T: %v", h, err)
		}
	}
}

func TestParsing(t *testing.T) {
	got := term.ParsePanes("ocp\t0\tmy win\t%7\t42\tzsh\t/a b\t99\t0\nbad line\nocp\t1\tx\tnot-a-pane\t1\tzsh\t/\t1\t0\n")
	if len(got) != 1 || got[0].WindowName != "my win" || got[0].Path != "/a b" || got[0].PID != 42 || got[0].Activity != 99 || got[0].Dead {
		t.Fatalf("%+v", got)
	}
	p := ParseProcs("  1     0 /sbin/launchd\n 42 1 /Applications/Some App.app/x\nnope\n")
	if len(p) != 2 || p[42].Comm != "/Applications/Some App.app/x" || p[42].PPID != 1 {
		t.Fatalf("%v", p)
	}
}
