package usage

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/team"
)

const clock = int64(1_800_000_000)

func setAgent(t *testing.T, q db.Q, agent, state, account string) {
	t.Helper()
	if err := db.SetAgentState(q, agent, state, 0, ""); err != nil {
		t.Fatal(err)
	}
	if account != "" {
		if err := db.SetAccount(q, agent, account, ""); err != nil {
			t.Fatal(err)
		}
	}
}

func event(t *testing.T, q db.Q, ts int64, agent, kind, account string) {
	t.Helper()
	if account != "" {
		if _, err := q.Exec("INSERT INTO events(ts, agent, account, kind) VALUES(?,?,?,?)", ts, agent, account, kind); err != nil {
			t.Fatal(err)
		}
		return
	}
	if _, err := db.AddEventAt(q, ts, agent, kind); err != nil {
		t.Fatal(err)
	}
}

func TestActiveCountsRunningMovedAndReservedAgentsPerAccount(t *testing.T) {
	q := openDB(t)
	e := testEnv(t.TempDir(), clock)
	setAgent(t, q, "run", "RUNNING", "a")
	setAgent(t, q, "idle", "IDLE", "a")
	setAgent(t, q, "moved", "IDLE", "b")
	event(t, q, clock-50, "moved", "moved_account", "")
	setAgent(t, q, "woken", "IDLE", "a")
	event(t, q, clock-100, "woken", "wake_reserved", "c")
	setAgent(t, q, "late", "IDLE", "a")
	event(t, q, clock-WakeReserveS-1, "late", "wake_reserved", "c") // too old to hold a slot
	setAgent(t, q, "seen", "IDLE", "a")
	event(t, q, clock-100, "seen", "wake_reserved", "c")
	event(t, q, clock-90, "seen", "Stop", "") // seen running: the reservation is spent
	setAgent(t, q, "failed", "IDLE", "a")
	event(t, q, clock-100, "failed", "wake_reserved", "c")
	event(t, q, clock-90, "failed", "wake_failed", "")
	setAgent(t, q, "closed", "RUNNING", "a")
	db.SetClosed(q, "closed", "s")
	by, err := e.ActiveByAccount(q)
	if err != nil {
		t.Fatal(err)
	}
	if len(by["a"]) != 1 || by["a"][0] != "run" || len(by["b"]) != 1 || by["b"][0] != "moved" || len(by["c"]) != 1 || by["c"][0] != "woken" {
		t.Fatalf("%v", by)
	}
	n, _ := e.ActiveAgents(q, "run")
	if n["a"] != 0 || n["b"] != 1 || n["c"] != 1 {
		t.Fatalf("a placement is counted against the slot it holds: %v", n)
	}
	// a moved agent counts only until its next Stop
	event(t, q, clock-10, "moved", "Stop", "")
	if by, _ = e.ActiveByAccount(q); len(by["b"]) != 0 {
		t.Fatalf("%v", by)
	}
}

func putMeta(t *testing.T, q db.Q, name string, v any) {
	t.Helper()
	b, _ := json.Marshal(v)
	if err := db.SetMeta(q, name, string(b)); err != nil {
		t.Fatal(err)
	}
}

func TestCostsPriceAnAgentByKindThenModelThenEveryoneThenTheStartingValue(t *testing.T) {
	q := openDB(t)
	home := t.TempDir()
	e := testEnv(home, clock)
	e.AgentConfig = func(agent string) (team.Config, error) {
		return team.Config{"model": map[string]string{"x": "opus", "y": "sonnet"}[agent], "effort": "high"}, nil
	}
	c, _ := e.LoadCosts(q)
	if p, src, n := c.Describe("x"); p != TypicalAgentBurnPerS || src != "starting value" || n != 0 {
		t.Fatalf("%v %q %d", p, src, n)
	}
	var rows []map[string]any
	for i := 0; i < 5; i++ {
		rows = append(rows, map[string]any{"t": i, "a": "a", "r": 0.004 + float64(i)*0.0001, "m": "opus", "e": "high"})
	}
	for i := 0; i < 5; i++ {
		rows = append(rows, map[string]any{"t": 9, "a": "a", "r": 0.01, "m": "sonnet", "e": nil})
	}
	putMeta(t, q, samplesMeta, rows)
	putMeta(t, q, weekRatioMeta, map[string]any{"at": 1, "r": map[string]float64{"a": 2.5, "*max_5x": 4}})
	c, _ = e.LoadCosts(q)
	p, src, n := c.Describe("x")
	if src != "opus high" || n != 5 || math.Abs(p-0.0042) > 1e-12 {
		t.Fatalf("%v %q %d", p, src, n)
	}
	if _, src, _ := c.Describe("y"); src != "sonnet" { // its kind has no effort-matched pool, so its model's
		t.Fatalf("%q", src)
	}
	if _, src, n := c.Describe(""); src != "all agents" || n != 10 {
		t.Fatalf("%q %d", src, n)
	}
	if r, ok := c.LongPerShort("a"); !ok || r != 2.5 {
		t.Fatalf("%v %v", r, ok)
	}
	e.PlanKey = func(string) string { return "max_5x" }
	c, _ = e.LoadCosts(q)
	if r, ok := c.LongPerShort("other"); !ok || r != 4 {
		t.Fatalf("the plan's pooled ratio: %v %v", r, ok)
	}
	// the estimate stays inside its bounds, and an even count takes the middle pair's mean
	putMeta(t, q, samplesMeta, []map[string]any{{"r": 1.0}, {"r": 1.0}, {"r": 1.0}, {"r": 1.0}, {"r": 1.0}, {"r": 1.0}})
	c, _ = e.LoadCosts(q)
	if p := c.PerS(""); p != CeilingPerS {
		t.Fatalf("%v", p)
	}
	if m := median([]float64{1, 2, 3, 10}); m != 2.5 {
		t.Fatalf("%v", m)
	}
}

func TestReadAllGivesEveryAccountItsCapAndFreshBurn(t *testing.T) {
	q := openDB(t)
	home := t.TempDir()
	e := testEnv(home, clock)
	for _, a := range []string{"a", "b"} {
		os.MkdirAll(filepath.Join(home, "accounts", a), 0o755)
	}
	writeConfig(t, home, map[string]any{"active_cap": 3, "active_cap_b": 1})
	if err := WriteBurn(home, "a", 0.01, clock-10); err != nil {
		t.Fatal(err)
	}
	WriteBurn(home, "b", 0.01, clock-601) // too old to describe the present
	setAgent(t, q, "run", "RUNNING", "b")
	loads, err := e.ReadAll(q, "")
	if err != nil {
		t.Fatal(err)
	}
	a, b := loads["a"], loads["b"]
	if a.Cap != 3 || b.Cap != 1 || a.Burn == nil || *a.Burn != 0.01 || b.Burn != nil || b.Active != 1 || !b.AtCap() || a.AtCap() {
		t.Fatalf("%+v %+v", a, b)
	}
}

func TestReadForAgentTakesContextFromTheAgentsOwnFreshSnapshotOnly(t *testing.T) {
	home := t.TempDir()
	e := testEnv(home, clock)
	live := map[string]any{"windows": []any{}, "collected_at": clock - 5, "ctx_pct": 40, "session_id": "own", "model_id": "m", "account": "a", "cost_usd": 2.5}
	writeStatus(t, home, "a", map[string]any{"windows": []any{}, "collected_at": clock - 5, "ctx_pct": 99, "session_id": "other"})
	write := func(agent string, v any) {
		os.MkdirAll(filepath.Join(home, "agents", agent), 0o755)
		b, _ := json.Marshal(v)
		os.WriteFile(filepath.Join(home, "agents", agent, "status.json"), b, 0o644)
		os.WriteFile(filepath.Join(home, "agents", agent, "agent.yaml"), nil, 0o644) // ListAgents lists folders that hold one
	}
	if st := e.ReadForAgent("none", ""); st.Account != "" || st.Exists {
		t.Fatalf("an agent on no account knows nothing: %+v", st)
	}
	if st := e.ReadForAgent("fresh", "a"); st.CtxPct != nil || st.SessionID != "" {
		t.Fatalf("never ran: another agent's context was borrowed: %+v", st)
	}
	write("x", live)
	if st := e.ReadForAgent("x", "a"); st.CtxPct == nil || *st.CtxPct != 40 || st.SessionID != "own" || st.ModelID != "m" {
		t.Fatalf("%+v", st)
	}
	stale := map[string]any{"windows": []any{}, "collected_at": clock - 5000, "ctx_pct": 40, "session_id": "old"}
	write("y", stale)
	if st := e.ReadForAgent("y", "a"); st.CtxPct != nil || st.SessionID != "" {
		t.Fatalf("a stale snapshot is not trusted: %+v", st)
	}
	write("z", "{broken")
	if st := e.ReadForAgent("z", "a"); st.SessionID != "" {
		t.Fatalf("%+v", st)
	}
	if got := e.ReportedAccount("x"); got != "a" {
		t.Fatalf("%q", got)
	}
	if e.ReportedAccount("y") != "" || e.ReportedAccount("nobody") != "" {
		t.Fatal("a stale or missing snapshot reports no account")
	}
	if c, ok := e.LiveCost("a"); !ok || c != 2.5 {
		t.Fatalf("%v %v", c, ok)
	}
	if _, ok := e.LiveCost("b"); ok {
		t.Fatal("cost on an account nothing reports on")
	}
}

func TestBurnIsReadBackOnlyWhileFresh(t *testing.T) {
	home := t.TempDir()
	e := testEnv(home, clock)
	if err := WriteBurn(home, "nope", 1, clock); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(home, "accounts", "nope")); err == nil {
		t.Fatal("a burn file made an account")
	}
	os.MkdirAll(filepath.Join(home, "accounts", "a"), 0o755)
	WriteBurn(home, "a", 0.02, clock-600)
	if b, ok := e.ReadBurn("a"); !ok || b != 0.02 {
		t.Fatalf("%v %v", b, ok)
	}
	os.WriteFile(filepath.Join(home, "accounts", "a", "burn.json"), []byte(`{"at": "x"}`), 0o644)
	if _, ok := e.ReadBurn("a"); ok {
		t.Fatal("a bad file is no reading")
	}
}

func TestAccountVerdictSaysWhyAnAccountCannotTakeWork(t *testing.T) {
	home := t.TempDir()
	e := testEnv(home, clock)
	writeStatus(t, home, "a", map[string]any{"collected_at": clock - 5, "windows": []any{
		map[string]any{"minutes": 300, "used_percent": 10, "resets_at": clock + 3600, "observed_at": clock - 5},
		map[string]any{"minutes": 10080, "used_percent": 10, "resets_at": clock + 90000, "observed_at": clock - 5}}})
	st := e.Read("a")
	ld := &Load{Account: "a", Active: 0, Cap: 2}
	if ok, why := e.AccountVerdict("a", st, ld, nil, false); ok || why != "not logged in" {
		t.Fatalf("%v %q", ok, why)
	}
	if ok, why := e.AccountVerdict("a", st, ld, nil, true); !ok || why != "5h 10%" {
		t.Fatalf("%v %q", ok, why)
	}
	if ok, why := e.AccountVerdict("a", st, ld, []Reason{{"a", "ranked"}}, true); !ok || why != "ranked" {
		t.Fatalf("%v %q", ok, why)
	}
	if ok, why := e.AccountVerdict("a", st, &Load{Account: "a", Active: 2, Cap: 2}, nil, true); ok || why != "5h 10%" {
		t.Fatalf("an account at its cap is allowed by usage and unfit: %v %q", ok, why)
	}
	os.WriteFile(filepath.Join(home, "accounts", "a", "vendor"), []byte("nonesuch\n"), 0o644)
	if ok, why := e.AccountVerdict("a", st, ld, nil, true); ok || why == "" {
		t.Fatalf("a vendor with no driver takes nothing: %v %q", ok, why)
	}
}

func TestAClosedRunningAgentHoldingAReservationCountsOnTheReservedAccountOnly(t *testing.T) {
	q := openDB(t)
	e := testEnv(t.TempDir(), clock)
	setAgent(t, q, "gone", "RUNNING", "a")
	db.SetClosed(q, "gone", "s")
	event(t, q, clock-100, "gone", "wake_reserved", "c")
	by, err := e.ActiveByAccount(q)
	if err != nil {
		t.Fatal(err)
	}
	if len(by["a"]) != 0 || len(by["c"]) != 1 {
		t.Fatalf("a closed session is not running on the account it last had: %v", by)
	}
}

func TestByKindPricesEachKindOnceAndSaysWhatItRestsOn(t *testing.T) {
	kinds := map[string][2]string{"a": {"opus", "high"}, "b": {"opus", "high"}, "c": {"haiku", "low"}}
	c := &Costs{kindOf: func(n string) (string, string, bool) { k, ok := kinds[n]; return k[0], k[1], ok }}
	got := c.ByKind([]string{"a", "b", "c", "gone"})
	want := []string{
		"haiku low " + fmt.Sprintf("%.2f", TypicalAgentBurnPerS*60) + "%/min (starting value)",
		"opus high " + fmt.Sprintf("%.2f", TypicalAgentBurnPerS*60) + "%/min (starting value)",
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("%q", got)
	}
}
