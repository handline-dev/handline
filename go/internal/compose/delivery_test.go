package compose

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	dbpkg "github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
)

// sendingReasons is the sending_reason of each message chat.messages shows for alice, through the real composition.
func sendingReasons(t *testing.T, p *Parts, home string) []any {
	t.Helper()
	srv := rpc.NewServer(founder{}, "x", store.LatestVersion(), "local", func(string, ...any) {})
	srv.DB = p.Sink.DB
	reads.Register(srv, reads.Env{DB: p.Sink.DB, Home: home, Delivery: p.Delivery()})
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
	d, home := p.Sink.DB, p.home
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
	if why, err := d.WallReason(p.Sink.DB, "alice", nil); why != "" || err != nil {
		t.Fatalf("%q %v", why, err)
	}
	if cause, err := d.ClosedWakeCause("alice", nil); cause != "" || err != nil {
		t.Fatalf("%q %v", cause, err)
	}
	if _, _, full := d.AccountFull("alice"); full {
		t.Fatal("no lifecycle, no full account")
	}
	if tr, err := d.Trigger(p.Sink.DB, "alice", nil, "a1"); tr != (reads.Trigger{}) || err != nil {
		t.Fatalf("%+v %v", tr, err)
	}
	if _, _, found, err := d.ForgottenResolve(p.Sink.DB, "alice", nil, 0); found || err != nil {
		t.Fatalf("%v %v", found, err)
	}
}

func TestTheTriggerIsWhatTheTickWouldHandTheAgentOffFor(t *testing.T) {
	p, lc, _, _ := wired(t)
	d, home := p.Sink.DB, p.home
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
	at(10)
	// alice is a structured agent: its triggers apply once its session is a live process. Her wake runs on in the background; the database
	// must outlive it (cleanups run last-in first).
	t.Cleanup(func() { p.Run.WaitIdle("alice", 10*time.Second) })
	if ok, said, err := lc.Manager.WakeAgent("alice", lifecycle.WakeOpts{}); err != nil || !ok {
		t.Fatalf("wake: %v %q %v", ok, said, err)
	}
	at(100)
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
