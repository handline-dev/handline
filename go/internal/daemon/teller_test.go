package daemon

import (
	"database/sql"
	"github.com/handline/handline/go/internal/db"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/handline/handline/go/internal/structured"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/term"
)

const tellTeam = `teams:
  eng: [alice, bob]
agents:
  alice: {}
  bob: {}
edges:
  - { from: "*", to: "human", kind: task }
  - { from: "*", to: "*", relation: peer, response: async }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
limits:
  max_chain_depth: 4
`

// typedAt is a structured agent port that keeps what was typed at each agent.
type typedAt struct {
	mu    sync.Mutex
	typed map[string][]string
}

func (a *typedAt) Structured(string) bool { return true }
func (a *typedAt) Live(string) bool       { return true }
func (a *typedAt) Send(agent, op, text string, _ structured.Delivery) (structured.Receipt, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.typed[agent] = append(a.typed[agent], text)
	return structured.Receipt{}, nil
}
func (a *typedAt) Withdraw(string, string) (structured.Receipt, bool, error) {
	return structured.Receipt{}, true, nil
}
func (a *typedAt) Promote(string, string) (structured.Receipt, error) {
	return structured.Receipt{}, nil
}
func (a *typedAt) said(agent string) string {
	a.mu.Lock()
	defer a.mu.Unlock()
	return strings.Join(a.typed[agent], "\n")
}

// The ports a structured-only daemon has no screen for: nothing is on a screen, so every telling goes through the agent port.
type noScreen struct{}

func (noScreen) Available() bool                             { return false }
func (noScreen) Missing() string                             { return "no terminal" }
func (noScreen) Alive(string) bool                           { return false }
func (noScreen) TypeableNow(string) bool                     { return false }
func (noScreen) ScreenBlocker(string) *term.Blocker          { return nil }
func (noScreen) SessionOnScreen(string) (bool, bool)         { return false, false }
func (noScreen) ComposerOnScreen(string) (string, bool)      { return "", false }
func (noScreen) IsTextTyped(string, string) bool             { return false }
func (noScreen) BackgroundWork(string) (string, bool)        { return "", false }
func (noScreen) LastTail(string) []string                    { return nil }
func (noScreen) Send(string, string, bool) (bool, string)    { return false, "no terminal" }
func (noScreen) SendToSession(string, string) (bool, string) { return false, "no terminal" }
func (noScreen) SendCtrlC(string)                            {}
func (noScreen) ClearContext(string) (bool, string)          { return false, "no terminal" }
func (noScreen) ClearCommand(string) string                  { return "/clear" }

// wiredPorts is every other port as an agent with no screen, no limit and nothing in the way: the telling layer can call it.
type wiredPorts struct{}

func (wiredPorts) LiveSessions() (map[string]bool, bool) {
	return map[string]bool{"alice": true, "bob": true}, true
}
func (wiredPorts) PastDialog(db.Q, string, bool) (string, string)                    { return "", "" }
func (wiredPorts) Reason(kind, detail string) string                                 { return kind + ": " + detail }
func (wiredPorts) DecisionAfterS() int64                                             { return 600 }
func (wiredPorts) WaitingOnPrompt(db.Q, *db.AgentState) bool                         { return false }
func (wiredPorts) AtLimit(db.Q, string) bool                                         { return false }
func (wiredPorts) NoticeDigestS(string) int64                                        { return 900 }
func (wiredPorts) CloseTimeoutS(string) int64                                        { return 600 }
func (wiredPorts) Never() int64                                                      { return -1 }
func (wiredPorts) BgGraceS(string) int64                                             { return 300 }
func (wiredPorts) ConfigSeconds(_ string, def int64) int64                           { return def }
func (wiredPorts) FooterCount(string) int                                            { return 1 }
func (wiredPorts) LiveSession(db.Q, string) string                                   { return "s1" }
func (wiredPorts) ContextUse(db.Q, string) (int64, int64)                            { return 5000, 200000 }
func (wiredPorts) KeepsTranscripts(string) bool                                      { return false }
func (wiredPorts) FindTranscript(string, string) string                              { return "" }
func (wiredPorts) MovableTrigger(string) bool                                        { return false }
func (wiredPorts) StartForWork(db.Q, string) string                                  { return "" }
func (wiredPorts) WakeAgent(db.Q, string) (bool, string)                             { return false, "" }
func (wiredPorts) QueuedOrIdle(db.Q, string, string) error                           { return nil }
func (wiredPorts) Marker(db.Q, string, string, map[string]any)                       {}
func (wiredPorts) RecordScreenSample(db.Q, string, *db.AgentState, string, []string) {}
func (wiredPorts) OpenProposalOf(int64) string                                       { return "" }
func (wiredPorts) Answered(db.Q, string, *db.Request, string) string                 { return "" }
func (wiredPorts) DeliveryBlock(db.Q, string) (string, bool)                         { return "", false }

func tellDaemon(t *testing.T, ports bool) (*Daemon, *typedAt) {
	t.Helper()
	t.Setenv("HANDLINE_TEST_ROOT", t.TempDir())
	home := t.TempDir()
	if err := os.WriteFile(filepath.Join(home, "team.yaml"), []byte(tellTeam), 0o644); err != nil {
		t.Fatal(err)
	}
	for _, a := range []string{"alice", "bob"} {
		dir := filepath.Join(home, "agents", a)
		os.MkdirAll(dir, 0o755)
		os.WriteFile(filepath.Join(dir, "agent.yaml"), []byte("vendor: claude\n"), 0o644)
	}
	agents := &typedAt{typed: map[string][]string{}}
	cfg := Config{Home: home, DBFile: filepath.Join(home, "db.sqlite"), Auth: pidAuth{}, Agents: agents, Log: t.Logf}
	if ports {
		cfg.OnOpen = func(_ *sql.DB, env *supervisor.Env) {
			env.Ports = supervisor.Ports{Agents: agents, Terminal: noScreen{}, Observer: wiredPorts{}, Dialogs: wiredPorts{}, Waits: wiredPorts{}, Idle: wiredPorts{}, Sessions: wiredPorts{},
				Lifecycle: wiredPorts{}, Evaluate: wiredPorts{}, Channel: wiredPorts{}, Proposals: wiredPorts{}, Stuck: wiredPorts{}}
		}
	}
	d, err := Start(cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(d.Close)
	return d, agents
}

func TestAMemoOnADoingTaskIsTypedAtItsHolderWhenThePortsAreWired(t *testing.T) {
	d, agents := tellDaemon(t, true)
	doing(t, d, "bob")
	f := pipeTo(t, d, "k")
	r := f.ask(`{"jsonrpc":"2.0","id":2,"method":"task.memo.add","params":{"task":1,"text":"mind the cache","request_id":"m1"}}`)
	if r["result"] == nil {
		t.Fatalf("%v", r)
	}
	if got := agents.said("bob"); !strings.Contains(got, "mind the cache") {
		t.Fatalf("the holder was not told: %q", got)
	}
	for _, c := range []struct{ call, want string }{
		{`{"jsonrpc":"2.0","id":3,"method":"task.memo.edit","params":{"task":1,"memo":1,"text":"mind the disk","request_id":"m2"}}`, "memo edited by human: mind the disk"},
		{`{"jsonrpc":"2.0","id":4,"method":"task.memo.delete","params":{"task":1,"memo":1,"request_id":"m3"}}`, "memo deleted by human"},
	} {
		result(t, f.ask(c.call))
		if got := agents.said("bob"); !strings.Contains(got, c.want) {
			t.Fatalf("%q is not in %q", c.want, got)
		}
	}
}

// doing opens T1 for the agent, queues it and makes it the one in hand.
func doing(t *testing.T, d *Daemon, agent string) {
	t.Helper()
	tid, err := db.NewTask(d.db, "job", agent, "alice", 0, db.DefaultPriority, "", 0)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.SetTaskQueued(d.db, tid, "human"); err != nil {
		t.Fatal(err)
	}
	d.env().AdvanceCurrentTask(d.db, agent)
}

func result(t *testing.T, r map[string]any) map[string]any {
	t.Helper()
	m, _ := r["result"].(map[string]any)
	if m == nil {
		t.Fatalf("%v", r)
	}
	return m
}

func TestAQueuedTaskGoesToAFreeOwnerAndTheReplySaysSo(t *testing.T) {
	d, agents := tellDaemon(t, true)
	tid, _ := db.NewTask(d.db, "job", "bob", "alice", 0, db.DefaultPriority, "", 0)
	f := pipeTo(t, d, "k")
	said := result(t, f.ask(`{"jsonrpc":"2.0","id":2,"method":"task.queue","params":{"tasks":[1],"request_id":"q1"}}`))["said"]
	if said != "task T1 is queued (human) - bob was free, so it went out now" {
		t.Fatalf("%v", said)
	}
	if got := agents.said("bob"); !strings.Contains(got, "job") {
		t.Fatalf("the task was not typed at its owner: %q", got)
	}
	_ = tid
	// the owner is busy now: the second is queued behind the first and the reply says only that
	db.NewTask(d.db, "second", "bob", "alice", 0, db.DefaultPriority, "", 0)
	said = result(t, f.ask(`{"jsonrpc":"2.0","id":3,"method":"task.queue","params":{"tasks":[2],"request_id":"q2"}}`))["said"]
	if !strings.HasPrefix(said.(string), "task T2 is queued (human)") || strings.Contains(said.(string), "went out") {
		t.Fatalf("%v", said)
	}
}

func TestADiscardTypesItsSaysAndMovesTheOwnerOn(t *testing.T) {
	d, agents := tellDaemon(t, true)
	doing(t, d, "bob")
	second, _ := db.NewTask(d.db, "the next one", "bob", "alice", 0, db.DefaultPriority, "", 0)
	db.SetTaskQueued(d.db, second, "human")
	reqs, _ := db.TaskRequests(d.db, 1)
	for _, q := range reqs {
		db.CloseIfQueued(d.db, q.ID, "dismissed", "", db.CloseOpts{})
	}
	agents.typed = map[string][]string{}
	f := pipeTo(t, d, "k")
	result(t, f.ask(`{"jsonrpc":"2.0","id":2,"method":"task.discard","params":{"task":1,"reason":"not needed","request_id":"d1"}}`))
	if got := agents.said("bob"); !strings.Contains(got, "T1 discarded by the founder: not needed") {
		t.Fatalf("the discard was not told: %q", got)
	}
	if cur, _, _ := db.CurrentTask(d.db, "bob"); cur != second {
		t.Fatalf("the owner was not moved on to T%d: %d", second, cur)
	}
}

func TestNoTellingWhenThePortsAreNotAllSet(t *testing.T) {
	d, agents := tellDaemon(t, false)
	if d.teller() != nil {
		t.Fatal("a Teller without every port is a nil call")
	}
	doing(t, d, "bob")
	agents.typed = map[string][]string{}
	f := pipeTo(t, d, "k")
	result(t, f.ask(`{"jsonrpc":"2.0","id":2,"method":"task.memo.add","params":{"task":1,"text":"mind the cache","request_id":"m1"}}`))
	if got := agents.said("bob"); got != "" {
		t.Fatalf("nothing is told: %q", got)
	}
}
