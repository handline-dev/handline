package usage

import (
	"encoding/json"
	"math"
	"os"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/team"
)

func burnEnv(t *testing.T) (*Env, db.Q, *int64) {
	t.Helper()
	now := clock
	e := &Env{Home: t.TempDir(), Now: func() float64 { return float64(now) }, PlanKey: func(string) string { return "pro" }}
	e.AgentConfig = func(agent string) (team.Config, error) {
		if agent == "gone" {
			return nil, os.ErrNotExist
		}
		return team.Config{"model": map[string]string{"x": "opus", "y": "opus", "z": "sonnet"}[agent], "effort": "high"}, nil
	}
	return e, openDB(t), &now
}

// liveStatus is a reading of the shortest window taken at `at`, as the poll leaves it.
func liveStatus(at int64, pct int, reset int64) *AccountStatus {
	p := pct
	return &AccountStatus{Account: "a", Exists: true, CollectedAt: &at, AsOf: ptrF(float64(at)),
		Short: AcctWindow{Minutes: 300, Pct: &p, Reset: reset, At: &at, Readable: true},
		Long:  AcctWindow{Minutes: 10080, Readable: true}}
}

func ptrF(f float64) *float64 { return &f }

func samples(t *testing.T, q db.Q) []map[string]any {
	t.Helper()
	rows, err := loadSampleRows(q)
	if err != nil {
		t.Fatal(err)
	}
	return rows
}

func TestObserveFilesAStretchOfWorkAsOneSamplePerAgent(t *testing.T) {
	e, q, _ := burnEnv(t)
	at := clock - 4000
	for _, step := range []struct {
		dt  int64
		pct int
	}{{0, 10}, {300, 11}, {700, 13}} {
		if err := e.Observe(q, "a", []string{"y", "x"}, liveStatus(at+step.dt, step.pct, clock+9000), nil); err != nil {
			t.Fatal(err)
		}
	}
	// who is working changes: the 700 s stretch closes.
	if err := e.Observe(q, "a", []string{"x"}, liveStatus(at+800, 13, clock+9000), nil); err != nil {
		t.Fatal(err)
	}
	got := samples(t, q)
	if len(got) != 1 {
		t.Fatalf("one sample: %v", got)
	}
	r, _ := num(got[0]["r"])
	if want := 3.0 / 700 / 2; math.Abs(r-want) > 1e-12 || got[0]["m"] != "opus" || got[0]["e"] != "high" || got[0]["a"] != "a" {
		t.Fatalf("sample %v, want rate %v of opus high", got[0], want)
	}
	if t1, _ := num(got[0]["t"]); int64(t1) != at+700 {
		t.Fatalf("sample is stamped at the stretch's end: %v", got[0]["t"])
	}
}

func TestObserveFilesNoModelWhenAgentsDiffer(t *testing.T) {
	e, q, _ := burnEnv(t)
	at := clock - 4000
	e.Observe(q, "a", []string{"x", "z"}, liveStatus(at, 10, 5), nil)
	e.Observe(q, "a", []string{"x", "z"}, liveStatus(at+900, 14, 5), nil)
	e.Observe(q, "a", nil, liveStatus(at+900, 14, 5), nil)
	got := samples(t, q)
	if len(got) != 1 || got[0]["m"] != nil || got[0]["e"] != nil {
		t.Fatalf("a rise cannot be split between kinds: %v", got)
	}
}

func TestObserveDropsWhatIsNotARate(t *testing.T) {
	cases := map[string]func(e *Env, q db.Q, at int64){
		"too short": func(e *Env, q db.Q, at int64) {
			e.Observe(q, "a", []string{"x"}, liveStatus(at, 10, 5), nil)
			e.Observe(q, "a", []string{"x"}, liveStatus(at+599, 12, 5), nil)
			e.Observe(q, "a", nil, liveStatus(at+599, 12, 5), nil)
		},
		"a window rolled over": func(e *Env, q db.Q, at int64) {
			e.Observe(q, "a", []string{"x"}, liveStatus(at, 10, 5), nil)
			e.Observe(q, "a", []string{"x"}, liveStatus(at+900, 12, 6), nil)
			e.Observe(q, "a", nil, liveStatus(at+900, 12, 6), nil)
		},
		"the figure fell": func(e *Env, q db.Q, at int64) {
			e.Observe(q, "a", []string{"x"}, liveStatus(at, 10, 5), nil)
			e.Observe(q, "a", []string{"x"}, liveStatus(at+900, 4, 5), nil)
			e.Observe(q, "a", nil, liveStatus(at+900, 4, 5), nil)
		},
		"a cold start": func(e *Env, q db.Q, at int64) {
			first := map[string]bool{"x": true}
			e.Observe(q, "a", []string{"x"}, liveStatus(at, 10, 5), first)
			e.Observe(q, "a", []string{"x"}, liveStatus(at+900, 12, 5), nil)
			e.Observe(q, "a", nil, liveStatus(at+900, 12, 5), nil)
		},
		"an agent with no config": func(e *Env, q db.Q, at int64) {
			e.Observe(q, "a", []string{"gone"}, liveStatus(at, 10, 5), nil)
			e.Observe(q, "a", []string{"gone"}, liveStatus(at+900, 12, 5), nil)
			e.Observe(q, "a", nil, liveStatus(at+900, 12, 5), nil)
		},
	}
	for name, run := range cases {
		t.Run(name, func(t *testing.T) {
			e, q, _ := burnEnv(t)
			run(e, q, clock-4000)
			if got := samples(t, q); len(got) != 0 {
				t.Fatalf("filed %v", got)
			}
		})
	}
}

func TestObserveClosesALongStretchAndStartsTheNext(t *testing.T) {
	e, q, _ := burnEnv(t)
	at := clock - 5000
	e.Observe(q, "a", []string{"x"}, liveStatus(at, 10, 5), nil)
	e.Observe(q, "a", []string{"x"}, liveStatus(at+SegMaxS, 20, 5), nil)
	if got := samples(t, q); len(got) != 1 {
		t.Fatalf("a run that never changes hands still yields samples: %v", got)
	}
	seg, _ := readSegment(q, "a")
	if seg == nil || seg.T0 != at+SegMaxS || seg.P0 != 20 || seg.Cold {
		t.Fatalf("the next stretch begins where this one ended: %+v", seg)
	}
}

func TestObserveKeepsOnlyTheNewestSamples(t *testing.T) {
	e, q, _ := burnEnv(t)
	var rows []map[string]any
	for i := 0; i < KeepTotal+5; i++ {
		rows = append(rows, map[string]any{"t": i, "a": "a", "r": 0.01, "m": nil, "e": nil})
	}
	b, _ := json.Marshal(rows)
	db.SetMeta(q, samplesMeta, string(b))
	if err := e.emit(q, "a", &segment{Agents: []string{"x"}, T0: 0, P0: 0, T1: 1000, P1: 5}); err != nil {
		t.Fatal(err)
	}
	got := samples(t, q)
	if len(got) != KeepTotal || got[KeepTotal-1]["a"] != "a" {
		t.Fatalf("%d kept", len(got))
	}
	if first, _ := num(got[0]["t"]); first != 6 {
		t.Fatalf("the oldest are dropped: first t %v", got[0]["t"])
	}
}

func TestObserveReadsAStretchItWroteAndShrugsOffGarbage(t *testing.T) {
	e, q, _ := burnEnv(t)
	db.SetMeta(q, segKey("a"), "{not json")
	if err := e.Observe(q, "a", []string{"x"}, liveStatus(clock-100, 10, 5), nil); err != nil {
		t.Fatal(err)
	}
	if seg, _ := readSegment(q, "a"); seg == nil || seg.P0 != 10 {
		t.Fatalf("a stretch that cannot be read is begun again: %+v", seg)
	}
	// nobody working: the stretch is cleared.
	e.Observe(q, "a", nil, liveStatus(clock-50, 10, 5), nil)
	if seg, _ := readSegment(q, "a"); seg != nil {
		t.Fatalf("%+v", seg)
	}
}

func rowOf(acct string, shortPct, longPct int, shortReset, longReset int64, extra map[string]any) sampleRow {
	w := func(m int, pct int, reset int64) map[string]any {
		e := map[string]any{"minutes": json.Number(itoa(m)), "used_percent": json.Number(itoa(pct)), "resets_at": json.Number(itoa(int(reset))),
			"carried": false, "lost": false}
		for k, v := range extra {
			e[k] = v
		}
		return e
	}
	b, _ := json.Marshal([]any{w(300, shortPct, shortReset), w(10080, longPct, longReset)})
	return sampleRow{"account": acct, "windows": string(b)}
}

func TestLongRatiosCountOnlyLivePairsInsideOnePairOfWindows(t *testing.T) {
	plan := func(string) string { return "max" }
	rows := []sampleRow{
		rowOf("a", 10, 20, 100, 900, nil),
		rowOf("a", 20, 25, 100, 900, nil), // +10 / +5
		rowOf("a", 40, 40, 100, 900, nil), // +20 / +15
		rowOf("a", 50, 41, 200, 900, nil), // the short window rolled: not a pair
		rowOf("a", 55, 44, 200, 900, nil), // +5 / +3
	}
	got := LongRatios(rows, plan)
	// pairs: 10/5, 20/15, 5/3 -> short 35, long 23
	want := 23.0 / 35
	if len(got) != 2 || math.Abs(got["a"]-want) > 1e-12 || got["*max"] != got["a"] {
		t.Fatalf("%v, want %v for the account and its pool", got, want)
	}
}

func TestLongRatiosNeedTenPointsAndSkipCarriedFiguresAndFalls(t *testing.T) {
	if got := LongRatios([]sampleRow{rowOf("a", 10, 20, 1, 9, nil), rowOf("a", 30, 29, 1, 9, nil)}, nil); len(got) != 0 {
		t.Fatalf("nine points of week is under the floor: %v", got)
	}
	if got := LongRatios([]sampleRow{rowOf("a", 10, 20, 1, 9, map[string]any{"carried": true}), rowOf("a", 30, 40, 1, 9, nil)}, nil); len(got) != 0 {
		t.Fatalf("a carried figure says nothing: %v", got)
	}
	if got := LongRatios([]sampleRow{rowOf("a", 30, 20, 1, 9, nil), rowOf("a", 10, 40, 1, 9, nil)}, nil); len(got) != 0 {
		t.Fatalf("a fall says nothing: %v", got)
	}
	got := LongRatios([]sampleRow{rowOf("a", 10, 20, 1, 9, nil), rowOf("a", 30, 40, 1, 9, nil)}, nil)
	if len(got) != 2 || got["a"] != 1 || got["*"] != 1 {
		t.Fatalf("a plan with none pools under the bare prefix: %v", got)
	}
}

func TestRefreshLongRatiosReadsTheLogAndRunsAtMostEveryTenMinutes(t *testing.T) {
	e, q, now := burnEnv(t)
	put := func(ts int64, short, long int) {
		w := func(m, pct int) map[string]any {
			return map[string]any{"minutes": m, "used_percent": pct, "resets_at": 77, "observed_at": ts, "carried": false, "lost": false}
		}
		b, _ := json.Marshal([]any{w(300, short), w(10080, long)})
		if _, err := q.Exec("INSERT INTO usage_samples(account, ts, exists_, windows) VALUES('a', ?, 1, ?)", ts, string(b)); err != nil {
			t.Fatal(err)
		}
	}
	put(*now-1000, 10, 20)
	put(*now-900, 30, 40)
	put(*now-5*86400, 1, 1) // older than the window: not read
	if err := e.RefreshLongRatios(q, *now, false); err != nil {
		t.Fatal(err)
	}
	c, err := e.LoadCosts(q)
	if err != nil {
		t.Fatal(err)
	}
	if r, ok := c.LongPerShort("a"); !ok || r != 1 {
		t.Fatalf("the reader sees the ratio the writer kept: %v %v", r, ok)
	}
	put(*now-800, 60, 100)
	e.RefreshLongRatios(q, *now+WeekRatioEveryS-1, false)
	if c, _ = e.LoadCosts(q); func() bool { r, _ := c.LongPerShort("a"); return r != 1 }() {
		t.Fatal("not recomputed inside ten minutes")
	}
	e.RefreshLongRatios(q, *now+WeekRatioEveryS, false)
	if c, _ = e.LoadCosts(q); func() bool { r, _ := c.LongPerShort("a"); return r == 1 }() {
		t.Fatal("recomputed after ten minutes")
	}
	e.RefreshLongRatios(q, *now+WeekRatioEveryS, true) // force
}

func TestRecordSampleWritesWhatTheStatusHeldAndPruneKeepsTwoWeeks(t *testing.T) {
	e, q, now := burnEnv(t)
	st := liveStatus(clock-30, 12, 777)
	st.Long = AcctWindow{Readable: false}
	if err := e.RecordSample(q, "a", st, 0); err != nil {
		t.Fatal(err)
	}
	if err := e.RecordSample(q, "a", &AccountStatus{Account: "b", Error: "unreadable", Short: AcctWindow{Readable: true}, Long: AcctWindow{Readable: true}}, *now-15*86400); err != nil {
		t.Fatal(err)
	}
	rows, err := readSamples(q, 0)
	if err != nil || len(rows) != 2 {
		t.Fatalf("%v %v", rows, err)
	}
	old, fresh := rows[0], rows[1]
	if old["error"] != "unreadable" || old["windows"] != "[]" || old["collected_at"] != nil {
		t.Fatalf("a status with no windows and an error: %v", old)
	}
	if fresh["ts"] != json.Number(itoa(int(clock))) || fresh["exists_"] != json.Number("1") {
		t.Fatalf("stamped now: %v", fresh)
	}
	es := entries(fresh)
	if len(es) != 2 || es[1]["used_percent"] != json.Number("12") || es[1]["resets_at"] != json.Number("777") || es[0]["readable"] != false ||
		es[0]["used_percent"] != nil {
		t.Fatalf("every figure as the status has it, the unreadable window (minutes 0) sorting first: %v", es)
	}
	n, err := e.PruneSamples(q)
	if err != nil || n != 1 {
		t.Fatalf("pruned %d %v", n, err)
	}
}

func TestFirstTurnAgentsAreThoseWhoHaveNotStoppedSinceTheirStart(t *testing.T) {
	_, q, _ := burnEnv(t)
	event(t, q, 1, "x", "SessionStart", "")
	event(t, q, 2, "y", "SessionStart", "")
	event(t, q, 3, "y", "Stop", "")
	event(t, q, 4, "z", "SessionStart", "")
	event(t, q, 5, "z", "Stop", "")
	event(t, q, 6, "z", "moved_account", "")
	got, err := FirstTurnAgents(q)
	if err != nil || len(got) != 2 || !got["x"] || !got["z"] {
		t.Fatalf("%v %v", got, err)
	}
}
