package usage

import (
	"os"
	"path/filepath"
	"testing"
)

// weekAt writes an account whose week is half over (it resets in half a week) with the given weekly figure.
func weekAt(t *testing.T, home, acct string, used int) {
	t.Helper()
	writeStatus(t, home, acct, map[string]any{"collected_at": clock - 5, "windows": []any{
		map[string]any{"minutes": 300, "used_percent": 10, "resets_at": clock + 3600, "observed_at": clock - 5},
		map[string]any{"minutes": 10080, "used_percent": used, "resets_at": clock + WeekS/2, "observed_at": clock - 5}}})
}

func TestTheBucketHoldsTheWeeksElapsedShareAndABurstLessWhatIsSpent(t *testing.T) {
	home := t.TempDir()
	e := testEnv(home, clock)
	weekAt(t, home, "a", 50)
	h, ok := e.Headroom("a")
	if !ok || h < 0.0999 || h > 0.1001 {
		t.Fatalf("half the week and a 10%% burst, 50%% spent: %v %v", h, ok)
	}
	weekAt(t, home, "a", 60)
	if h, _ := e.Headroom("a"); h < -0.0001 || h > 0.0001 {
		t.Fatalf("spending the burst empties the bucket: %v", h)
	}
	writeStatus(t, home, "a", map[string]any{"collected_at": clock - 5, "windows": []any{}})
	if _, ok := e.Headroom("a"); ok {
		t.Fatal("a weekly figure nobody can read says nothing about pace")
	}
}

func TestAFastPaceWithLowCutoffRiskKeepsTheFullCap(t *testing.T) {
	q := openDB(t)
	home := t.TempDir()
	e := testEnv(home, clock)
	writeConfig(t, home, map[string]any{"active_cap": 4})
	for _, acct := range []string{"ahead", "burning"} {
		os.MkdirAll(filepath.Join(home, "accounts", acct), 0o755)
		weekAt(t, home, acct, 80) // far ahead of its week: 80% spent half way through
	}
	// 5h at 10%, an hour to its reset: 0.01%/min lasts far past it.
	if err := WriteBurn(home, "burning", 0.01/60, clock); err != nil {
		t.Fatal(err)
	}
	loads, err := e.ReadAll(q, "")
	if err != nil {
		t.Fatal(err)
	}
	for _, acct := range []string{"ahead", "burning"} {
		if got := loads[acct].Cap; got != 4 {
			t.Errorf("%s: cap %d, want the configured 4 - a fast pace alone is no risk", acct, got)
		}
		if got := e.CapNow(q, acct); got != 4 {
			t.Errorf("%s: CapNow %d, want 4", acct, got)
		}
	}
}

func TestAHighCutoffRiskLowersTheCap(t *testing.T) {
	q := openDB(t)
	home := t.TempDir()
	e := testEnv(home, clock)
	writeConfig(t, home, map[string]any{"active_cap": 4})
	os.MkdirAll(filepath.Join(home, "accounts", "hot"), 0o755)
	weekAt(t, home, "hot", 20) // behind its week
	// 5h at 10%: 90 points left, the reset an hour off. 3%/min reaches the block in 30 minutes, half the way there.
	if err := WriteBurn(home, "hot", 3.0/60, clock); err != nil {
		t.Fatal(err)
	}
	loads, err := e.ReadAll(q, "")
	if err != nil {
		t.Fatal(err)
	}
	if got := loads["hot"].Cap; got != 2 {
		t.Fatalf("cap %d, want 2: half the burn is what lasts to the reset", got)
	}
	if got := e.CapNow(q, "hot"); got != 2 {
		t.Fatalf("CapNow %d, want 2", got)
	}
}

func TestCutoffShareJudgesEachWindowAgainstItsOwnReset(t *testing.T) {
	home := t.TempDir()
	e := testEnv(home, clock)
	writeStatus(t, home, "a", map[string]any{"collected_at": clock - 5, "windows": []any{
		map[string]any{"minutes": 300, "used_percent": 50, "resets_at": clock + 3600, "observed_at": clock - 5},
		map[string]any{"minutes": 10080, "used_percent": 90, "resets_at": clock + 86400, "observed_at": clock - 5}}})
	st, now := e.Read("a"), float64(clock)
	if got := CutoffShare(st, 0, 0, false, now); got != 1 {
		t.Fatalf("no burn, no risk: %v", got)
	}
	// 50 points over an hour: 0.5/60 %/s lasts 100 minutes, past the reset.
	if got := CutoffShare(st, 0.5/60, 0, false, now); got != 1 {
		t.Fatalf("a burn that outlasts its reset: %v", got)
	}
	// The week: 10 points over a day, at a tenth of the shortest window's burn: 0.05%/min runs out in 200 minutes.
	if got := CutoffShare(st, 0.5/60, 0.1, true, now); got < 0.138 || got > 0.139 {
		t.Fatalf("the week cut off first: %v", got)
	}
}

func TestCutoffCapRoundsUpSoAnyShareLeftKeepsOneSlot(t *testing.T) {
	for _, c := range []struct {
		base int
		s    float64
		want int
	}{{2, 2, 2}, {2, 1, 2}, {2, 0.5, 1}, {2, 0.01, 1}, {2, 0, 0}, {3, 0.5, 2}, {4, 0.9, 4}} {
		if got := CutoffCap(c.base, c.s); got != c.want {
			t.Errorf("CutoffCap(%d, %v) = %d, want %d", c.base, c.s, got, c.want)
		}
	}
}
