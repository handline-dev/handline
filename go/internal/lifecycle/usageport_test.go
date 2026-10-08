package lifecycle

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/usage"
)

const usageNow = int64(1_800_000_000)

func usageHome(t *testing.T, accounts map[string]map[string]any) *usage.Env {
	t.Helper()
	home := t.TempDir()
	for name, raw := range accounts {
		dir := filepath.Join(home, "accounts", name)
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		if raw != nil {
			b, _ := json.Marshal(raw)
			os.WriteFile(filepath.Join(dir, "status.json"), b, 0o644)
		}
	}
	return &usage.Env{Home: home, Now: func() float64 { return float64(usageNow) },
		LoginState: func(string) (bool, bool) { return true, true }, PlanKey: func(string) string { return "" },
		AgentConfig: func(string) (team.Config, error) { return team.Config{}, nil }}
}

func liveAccount(short, week int, weekResetIn int64) map[string]any {
	win := func(minutes, pct int, resetIn int64) map[string]any {
		return map[string]any{"minutes": minutes, "used_percent": pct, "resets_at": usageNow + resetIn, "observed_at": usageNow - 5, "carried": false, "lost": false}
	}
	return map[string]any{"collected_at": usageNow - 5, "windows": []any{win(300, short, 3600), win(10080, week, weekResetIn)}}
}

func TestTheUsageAdapterPicksTheAccountFurthestBehindItsPaceAndCountsLoadWithoutTheAgent(t *testing.T) {
	env := usageHome(t, map[string]map[string]any{"soon": liveAccount(50, 10, 3600), "later": liveAccount(5, 10, 90000), "never": nil})
	q := openDB(t)
	u := &UsageAdapter{Env: env, Q: q}
	if got := u.PickAccount([]string{"soon", "later", "never"}, ""); got != "soon" {
		t.Fatalf("%q", got)
	}
	if got := u.PickAccount([]string{"never"}, ""); got != "never" {
		t.Fatalf("a never-run account is a real place to go: %q", got)
	}
	if got := u.PickAccount([]string{}, ""); got != "" {
		t.Fatalf("no candidate picks nobody: %q", got)
	}
	// "soon" is full of agents; the one being placed holds one of the slots and is left out of the count
	for _, a := range []string{"w1", "w2"} {
		db.SetAgentState(q, a, states.Running, 0, "")
		db.SetAccount(q, a, "soon", "")
	}
	if got := u.PickAccount([]string{"soon", "later"}, ""); got != "later" {
		t.Fatalf("soon is at its cap: %q", got)
	}
	if got := u.PickAccount([]string{"soon", "later"}, "w1"); got != "soon" {
		t.Fatalf("the agent being placed holds one of the slots: %q", got)
	}
	if n := u.ActiveOn(q, "soon", "w1"); n != 1 {
		t.Fatalf("%d", n)
	}
	if u.CapOf("soon") != usage.DefaultActiveCap {
		t.Fatalf("%d", u.CapOf("soon"))
	}
	if u.Fit("soon", "w1") != true || u.Fit("soon", "") != false {
		t.Fatalf("fit: with the agent left out soon has a free slot; without, it is at its cap")
	}
	if !u.NotFor("soon", "opencode") || u.NotFor("soon", "claude") {
		t.Fatal("another vendor's account is not for this agent")
	}
	st := u.Read("later")
	if !st.Exists || st.Stale || st.ShortTrusted == nil || *st.ShortTrusted != 5 || st.ShortLabel != "5h" || st.LongLabel != "7d" {
		t.Fatalf("%+v", st)
	}
}

func TestAStructuredAgentsSessionIsTheOneStoredWithIt(t *testing.T) {
	env := usageHome(t, map[string]map[string]any{"a": liveAccount(5, 5, 90000)})
	q := openDB(t)
	db.SetAgentState(q, "s", states.Idle, 0, "")
	db.SetResumeSession(q, "s", "stored-id")
	s := &StatusAdapter{Env: env, Q: q, Structured: func(a string) bool { return a == "s" }}
	if got := s.ReadForAgent("s", "a"); got.SessionID != "stored-id" || got.CtxPct != nil {
		t.Fatalf("%+v", got)
	}
	// a terminal agent with no snapshot of its own knows no session
	if got := s.ReadForAgent("t", "a"); got.SessionID != "" {
		t.Fatalf("%+v", got)
	}
	if got := s.ReadForAgent("s", ""); got.SessionID != "stored-id" {
		t.Fatalf("a structured agent on no recorded account still has its session: %+v", got)
	}
}
