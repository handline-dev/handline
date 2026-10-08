package usage

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/store"
)

// status_cases.json is what the Python status module says about each case (tools/gen_usage_fixtures.py); the Go port must say the same.

type fixture struct {
	Now   int64         `json:"now"`
	Cases []statusCase  `json:"cases"`
	Picks []pickFixture `json:"picks"`
}

type loadSpec struct {
	Active  int                `json:"active"`
	Cap     int                `json:"cap"`
	Burn    *float64           `json:"burn"`
	Samples []float64          `json:"samples"`
	Ratios  map[string]float64 `json:"ratios"`
}

type statusCase struct {
	Name   string         `json:"name"`
	Status any            `json:"status"`
	Config map[string]any `json:"config"`
	Load   loadSpec       `json:"load"`
	Expect map[string]any `json:"expect"`
}

type pickFixture struct {
	Name     string `json:"name"`
	Accounts map[string]struct {
		Status any      `json:"status"`
		Load   loadSpec `json:"load"`
		Vendor string   `json:"vendor"`
	} `json:"accounts"`
	Candidates []string       `json:"candidates"`
	Config     map[string]any `json:"config"`
	Removing   []string       `json:"removing"`
	Opts       struct {
		Candidates []string          `json:"candidates"`
		Bootstrap  bool              `json:"bootstrap"`
		Exclude    map[string]string `json:"exclude"`
		Prefer     string            `json:"prefer"`
		Vendor     string            `json:"vendor"`
	} `json:"opts"`
	Expect struct {
		Picked  string     `json:"picked"`
		Reasons [][]string `json:"reasons"`
	} `json:"expect"`
}

func loadFixture(t *testing.T) fixture {
	t.Helper()
	b, err := os.ReadFile("testdata/status_cases.json")
	if err != nil {
		t.Fatal(err)
	}
	var f fixture
	if err := json.Unmarshal(b, &f); err != nil {
		t.Fatal(err)
	}
	time.Local = time.UTC
	return f
}

func writeStatus(t *testing.T, home, acct string, raw any) {
	t.Helper()
	dir := filepath.Join(home, "accounts", acct)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	f := filepath.Join(dir, "status.json")
	switch v := raw.(type) {
	case nil:
		os.Remove(f)
	case string:
		os.WriteFile(f, []byte(v), 0o644)
	default:
		b, _ := json.Marshal(v)
		os.WriteFile(f, b, 0o644)
	}
}

func writeConfig(t *testing.T, home string, cfg map[string]any) {
	t.Helper()
	var lines []string
	for k, v := range cfg {
		val, _ := json.Marshal(v) // a JSON scalar is a YAML one
		lines = append(lines, k+": "+string(val))
	}
	sort.Strings(lines)
	if err := os.WriteFile(filepath.Join(home, "config.yaml"), []byte(strings.Join(lines, "\n")+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
}

func testEnv(home string, now int64) *Env {
	return &Env{Home: home, Now: func() float64 { return float64(now) },
		LoginState: func(string) (bool, bool) { return true, true }, PlanKey: func(string) string { return "" }}
}

func (s loadSpec) load(acct string, e *Env) *Load {
	c := &Costs{longRatios: s.Ratios}
	if c.longRatios == nil {
		c.longRatios = map[string]float64{}
	}
	for _, r := range s.Samples {
		c.samples = append(c.samples, Sample{R: r})
	}
	return &Load{Account: acct, Active: s.Active, Cap: capOr2(s.Cap), Burn: s.Burn, Costs: c}
}

func winOut(w AcctWindow) map[string]any {
	var pct, at any
	if w.Pct != nil {
		pct = *w.Pct
	}
	if w.At != nil {
		at = *w.At
	}
	var reset any
	if w.Reset != 0 {
		reset = w.Reset
	}
	return map[string]any{"minutes": w.Minutes, "pct": pct, "reset": reset, "at": at, "carried": w.Carried, "lost": w.Lost, "readable": w.Readable}
}

func fl(p *float64) any {
	if p == nil {
		return nil
	}
	return *p
}

func ip(p *int) any {
	if p == nil {
		return nil
	}
	return *p
}

func describeGo(st *AccountStatus, ld *Load, now float64) map[string]any {
	ok, why := st.Assignable(0)
	if st.Error != "" {
		why = strings.SplitN(why, ":", 2)[0]
	}
	twOK, twWhy := st.TakesWork(1)
	if st.Error != "" {
		twWhy = strings.SplitN(twWhy, ":", 2)[0]
	}
	roOK, roWhy := st.RolledOver()
	ifOK, ifWhy := st.IdleFloor(0)
	shown, mark := st.ShownShort()
	var a1 any
	if st.Error == "" {
		o1, w1 := st.Assignable(1)
		a1 = []any{o1, w1}
	}
	var ctxUsed, trustedUsed, cost any
	if st.CtxUsed != nil {
		ctxUsed = *st.CtxUsed
	}
	if u := st.TrustedCtxUsed(); u != nil {
		trustedUsed = *u
	}
	if st.CostUSD != nil {
		cost = *st.CostUSD
	}
	pace := LongPace(st)
	var paceOut any = pace
	if math.IsInf(pace, 1) {
		paceOut = "inf"
	}
	var sessionID, modelID any
	if st.SessionID != "" {
		sessionID = st.SessionID
	}
	if st.ModelID != "" {
		modelID = st.ModelID
	}
	var subEnd any
	if st.SubEnd != 0 {
		subEnd = st.SubEnd
	}
	out := map[string]any{
		"exists": st.Exists, "error": st.Error != "", "unmetered": st.Unmetered,
		"short": winOut(st.Short), "long": winOut(st.Long),
		"short_source": st.ShortSource(), "short_trusted": ip(st.ShortTrusted()),
		"long_source": st.LongSource(), "long_trusted": ip(st.LongTrusted()), "stale": st.Stale(),
		"age_s": fl(st.AgeS()), "short_age_s": fl(st.ShortAgeS()), "live": st.IsLiveReading(),
		"assignable": []any{ok, why}, "assignable_active1": a1, "takes_work": []any{twOK, twWhy},
		"rolled_over": []any{roOK, roWhy}, "idle_floor": []any{ifOK, ifWhy}, "last_known": st.LastKnown(),
		"shown_short": []any{ip(shown), mark}, "window_report": st.WindowReport(),
		"short_headroom": ip(st.ShortHeadroom()), "long_headroom": ip(st.LongHeadroom()),
		"long_pace": paceOut, "unfit": emptyNil(Unfit(st, ld, "")), "unfit_agent": emptyNil(Unfit(st, ld, "")),
		"trusted_ctx_pct": ip(st.TrustedCtxPct()), "trusted_ctx_used": trustedUsed, "ctx_pct": ip(st.CtxPct), "ctx_used": ctxUsed,
		"session_id": sessionID, "model_id": modelID, "cost_usd": cost,
		"sub_end": subEnd, "last_week_first": st.LastWeekFirst,
	}
	for _, r := range []struct {
		key  string
		burn *float64
	}{{"runway_none", nil}, {"runway_burn", ptr(0.5 / 60)}} {
		out[r.key] = RunwayOf(st, r.burn, now, ld).Describe()
	}
	return out
}

// capOr2: a fixture that names no cap gets the Python generator's default.
func capOr2(n int) int {
	if n == 0 {
		return 2
	}
	return n
}

func emptyNil(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func ptr[T any](v T) *T { return &v }

// normalized round-trips through JSON so integers and floats compare the way the fixture holds them.
func normalized(t *testing.T, v any) any {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	var out any
	json.Unmarshal(b, &out)
	zeroResetIsNone(out)
	return out
}

// zeroResetIsNone: Python keeps a reset of 0 as 0 and a missing one as None; both mean "no reset" everywhere they are read.
func zeroResetIsNone(v any) {
	switch x := v.(type) {
	case map[string]any:
		for k, e := range x {
			if k == "reset" && e == float64(0) {
				x[k] = nil
			}
			zeroResetIsNone(e)
		}
	case []any:
		for _, e := range x {
			zeroResetIsNone(e)
		}
	}
}

func TestStatusMatchesPython(t *testing.T) {
	f := loadFixture(t)
	for _, c := range f.Cases {
		t.Run(c.Name, func(t *testing.T) {
			home := t.TempDir()
			e := testEnv(home, f.Now)
			writeConfig(t, home, c.Config)
			writeStatus(t, home, "a", c.Status)
			st := e.Read("a")
			got := normalized(t, describeGo(st, c.Load.load("a", e), float64(f.Now)))
			want := normalized(t, c.Expect)
			if !reflect.DeepEqual(got, want) {
				g, w := got.(map[string]any), want.(map[string]any)
				for k := range w {
					if !reflect.DeepEqual(g[k], w[k]) {
						t.Errorf("%s: go %v, python %v", k, g[k], w[k])
					}
				}
			}
		})
	}
}

func TestPickMatchesPython(t *testing.T) {
	f := loadFixture(t)
	for _, p := range f.Picks {
		t.Run(p.Name, func(t *testing.T) {
			home := t.TempDir()
			e := testEnv(home, f.Now)
			writeConfig(t, home, p.Config)
			loads := map[string]*Load{}
			for _, n := range p.Candidates {
				a := p.Accounts[n]
				writeStatus(t, home, n, a.Status)
				if a.Vendor != "" {
					os.WriteFile(filepath.Join(home, "accounts", n, "vendor"), []byte(a.Vendor+"\n"), 0o644)
				}
				loads[n] = a.Load.load(n, e)
			}
			for _, n := range p.Removing {
				os.WriteFile(filepath.Join(home, "accounts", n, ".removing"), nil, 0o644)
			}
			cands := p.Candidates
			picked, reasons, err := e.Pick(openDB(t), PickOpts{Candidates: cands, Bootstrap: p.Opts.Bootstrap, Exclude: p.Opts.Exclude, Load: loads,
				Prefer: p.Opts.Prefer, Vendor: p.Opts.Vendor})
			if err != nil {
				t.Fatal(err)
			}
			var got [][]string
			for _, r := range reasons {
				got = append(got, []string{r.Account, r.Why})
			}
			if picked != p.Expect.Picked || !reflect.DeepEqual(got, p.Expect.Reasons) {
				t.Errorf("go picked %q %v\npython picked %q %v", picked, got, p.Expect.Picked, p.Expect.Reasons)
			}
		})
	}
}

func openDB(t *testing.T) db.Q {
	t.Helper()
	d, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	return d
}
