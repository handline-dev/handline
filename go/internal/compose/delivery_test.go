package compose

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	dbpkg "github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/team"
)

// sendingReasons is the sending_reason of each message chat.messages shows for alice, through the real composition.
func sendingReasons(t *testing.T, p *Parts, home string) []any {
	t.Helper()
	srv := rpc.NewServer(founder{}, "x", store.LatestVersion(), "local", func(string, ...any) {})
	srv.DB = p.db
	reads.Register(srv, reads.Env{DB: p.db, Home: home, Delivery: p.Delivery()})
	r := call(t, srv, `{"jsonrpc":"2.0","id":2,"method":"chat.messages","params":{"agent":"alice","scope":"all"}}`)[0]
	b, _ := json.Marshal(r)
	if r["result"] != nil {
		b, _ = json.Marshal(r["result"])
	}
	var out struct {
		Messages []struct {
			ID            int64
			SendingReason any `json:"sending_reason"`
		}
	}
	if json.Unmarshal(b, &out) != nil || len(out.Messages) == 0 {
		t.Fatalf("%s", b)
	}
	var reasons []any
	for _, m := range out.Messages {
		reasons = append(reasons, m.SendingReason)
	}
	return reasons
}

func TestAClosedAgentsQueuedLineSaysWhyThroughTheRealComposition(t *testing.T) {
	p, _, _, _ := wired(t) // alice is closed, with one request from the person that nothing has delivered
	d, home := p.db, p.home
	if err := dbpkg.SetAccount(d, "alice", "a1", "test"); err != nil {
		t.Fatal(err)
	}
	// work is waiting: the next tick wakes it
	if got := sendingReasons(t, p, home); len(got) != 1 || got[0] != "closed, waking" {
		t.Fatalf("a closed agent with work waiting: %v", got)
	}
	// its account is at the wall
	now := time.Now().Unix()
	status, _ := json.Marshal(map[string]any{"collected_at": now, "windows": []any{
		map[string]any{"minutes": 300, "observed_at": now, "resets_at": now + 3600, "used_percent": 100},
		map[string]any{"minutes": 10080, "observed_at": now, "resets_at": now + 86400, "used_percent": 10}}})
	os.WriteFile(filepath.Join(home, "accounts", "a1", "status.json"), status, 0o644)
	if got := sendingReasons(t, p, home); got[0] != "closed, account full" {
		t.Fatalf("a closed agent whose account is full: %v", got)
	}
	// nothing is left to wake it for: the one request is settled
	os.Remove(filepath.Join(home, "accounts", "a1", "status.json"))
	if _, err := d.Exec("UPDATE requests SET state = 'answered' WHERE target = 'alice'"); err != nil {
		t.Fatal(err)
	}
	e := reads.Env{DB: d, Home: home, Delivery: p.Delivery()}
	if got, err := e.QueueReason("alice", 1); err != nil || got != "closed, nothing to wake it" {
		t.Fatalf("a closed agent with nothing to wake it: %q %v", got, err)
	}
	// the composition's answers are the lifecycle's own
	row, _ := dbpkg.GetAgentState(d, "alice")
	if cause, err := p.Delivery().ClosedWakeCause("alice", row); err != nil || cause != "" {
		t.Fatalf("no cause once settled: %q %v", cause, err)
	}
}

func TestTheDeliveryKnowsNothingBeforeTheLifecycleIsBuilt(t *testing.T) {
	p, _ := rig(t)
	d := p.Delivery()
	if why, err := d.WallReason(p.db, "alice", nil); why != "" || err != nil {
		t.Fatalf("%q %v", why, err)
	}
	if cause, err := d.ClosedWakeCause("alice", nil); cause != "" || err != nil {
		t.Fatalf("%q %v", cause, err)
	}
	if _, _, full := d.AccountFull("alice"); full {
		t.Fatal("no lifecycle, no full account")
	}
	if tr, err := d.Trigger(p.db, "alice", nil, "a1"); tr != (reads.Trigger{}) || err != nil {
		t.Fatalf("%+v %v", tr, err)
	}
	if _, _, found, err := d.ForgottenResolve(p.db, "alice", nil, 0); found || err != nil {
		t.Fatalf("%v %v", found, err)
	}
}

func TestTheTriggerOfAClosedAgentWhoseWakeIsWalledIsTheWaitTheDaemonPrints(t *testing.T) {
	p, lc, _, _ := wired(t) // alice is closed, with one request from the person that nothing has delivered
	d, home := p.db, p.home
	if err := dbpkg.SetAccount(d, "alice", "a1", "test"); err != nil {
		t.Fatal(err)
	}
	now := time.Now().Unix()
	status, _ := json.Marshal(map[string]any{"collected_at": now, "windows": []any{
		map[string]any{"minutes": 300, "observed_at": now, "resets_at": now + 3600, "used_percent": 100},
		map[string]any{"minutes": 10080, "observed_at": now, "resets_at": now + 86400, "used_percent": 10}}})
	os.WriteFile(filepath.Join(home, "accounts", "a1", "status.json"), status, 0o644)
	row, _ := dbpkg.GetAgentState(d, "alice")
	want := lc.Launch.WakeWaitReason(d, "alice", row)
	got, err := p.Delivery().Trigger(d, "alice", row, "a1")
	if err != nil || want == "" || got != (reads.Trigger{Reason: want}) {
		t.Fatalf("%+v %v, want the reason %q", got, err, want)
	}
	// agents.list says the same sentence in its status line, and nothing once the request is settled
	srv := rpc.NewServer(founder{}, "x", store.LatestVersion(), "local", func(string, ...any) {})
	srv.DB = d
	reads.Register(srv, reads.Env{DB: d, Home: home, Delivery: p.Delivery()})
	said := func() any {
		t.Helper()
		r := call(t, srv, `{"jsonrpc":"2.0","id":2,"method":"agents.list","params":{"status":true}}`)[0]
		for _, a := range r["result"].(map[string]any)["agents"].([]any) {
			if a := a.(map[string]any); a["name"] == "alice" {
				return a["wake_wait_reason"]
			}
		}
		t.Fatalf("alice is not listed: %v", r)
		return nil
	}
	if got := said(); got != want {
		t.Fatalf("agents.list: %v, want %q", got, want)
	}
	if _, err := d.Exec("UPDATE requests SET state = 'answered' WHERE target = 'alice'"); err != nil {
		t.Fatal(err)
	}
	if got := said(); got != nil {
		t.Fatalf("nothing wakes it, so nothing is walled: %v", got)
	}
}

func TestTheTriggerIsWhatTheTickWouldHandTheAgentOffFor(t *testing.T) {
	p, _, _, wt := wired(t)
	d, home := p.db, p.home
	if err := dbpkg.SetAccount(d, "alice", "a1", "test"); err != nil {
		t.Fatal(err)
	}
	at := func(pct int) {
		now := time.Now().Unix()
		status, _ := json.Marshal(map[string]any{"collected_at": now, "windows": []any{
			map[string]any{"minutes": 300, "observed_at": now, "resets_at": now + 3600, "used_percent": pct},
			map[string]any{"minutes": 10080, "observed_at": now, "resets_at": now + 86400, "used_percent": 10}}})
		os.WriteFile(filepath.Join(home, "accounts", "a1", "status.json"), status, 0o644)
	}
	fire := func() string {
		t.Helper()
		row, _ := dbpkg.GetAgentState(d, "alice")
		got, err := p.Delivery().Trigger(d, "alice", row, "a1")
		if err != nil {
			t.Fatal(err)
		}
		return got.Kind
	}
	at(100)
	if got := fire(); got != "" {
		t.Fatalf("a closed agent's triggers do not apply: %q", got)
	}
	// Her triggers apply once her session runs in a terminal.
	if err := dbpkg.ClearClosed(d, "alice"); err != nil {
		t.Fatal(err)
	}
	if err := dbpkg.SetTerminal(d, "alice", "%1", wt); err != nil {
		t.Fatal(err)
	}
	if got := fire(); got != "usage" {
		t.Fatalf("at the wall: %q", got)
	}
	at(10)
	if got := fire(); got != "" {
		t.Fatalf("below it: %q", got)
	}
	at(100)
	if _, err := d.Exec("UPDATE agent_state SET state = 'PARKED' WHERE agent = 'alice'"); err != nil {
		t.Fatal(err)
	}
	if got := fire(); got != "" {
		t.Fatalf("a parked agent is past its wall: %q", got)
	}
}

func TestTheTriggerJudgesContextByTheAgentsOwnLimit(t *testing.T) {
	p, _, wt := termTickRig(t)
	d := p.db
	os.WriteFile(filepath.Join(p.home, "agents", "bob", "agent.yaml"), []byte("vendor: claude\ncontext_tokens: 50000\n"), 0o644)
	sid := "44444444-4444-4444-8444-444444444444"
	writeSession(t, p, "a1", os.Getpid(), `{"cwd":"`+wt+`","sessionId":"`+sid+`","status":"idle"}`)
	dir, _ := team.AccountConfigDir(p.home, "a1")
	os.MkdirAll(filepath.Join(dir, "projects", "x"), 0o755)
	row, _ := dbpkg.GetAgentState(d, "bob")
	// trigger is what the delivery says once the newest reply of his session holds used tokens
	trigger := func(used int) reads.Trigger {
		t.Helper()
		reply := fmt.Sprintf(`{"type":"assistant","message":{"model":"claude-opus-5","usage":{"input_tokens":%d}}}`+"\n", used)
		os.WriteFile(filepath.Join(dir, "projects", "x", sid+".jsonl"), []byte(reply), 0o644)
		got, err := p.Delivery().Trigger(d, "bob", row, "a1")
		if err != nil {
			t.Fatal(err)
		}
		return got
	}
	if got := trigger(40000); !got.Applies || got.Fired || !strings.HasSuffix(got.Reason, "context 40,000 < 50,000") {
		t.Fatalf("under his own limit: %+v", got)
	}
	// 60,000 is far under the 150,000 every agent has
	if got, want := trigger(60000), (reads.Trigger{Applies: true, Fired: true, Kind: "context", Reason: "context 60,000 >= 50,000"}); got != want {
		t.Fatalf("over his own limit: %+v", got)
	}
}
