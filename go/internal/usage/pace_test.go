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

func TestThePaceShrinksTheCapAndAnAccountAheadOfItsWeekTakesNoNewAgent(t *testing.T) {
	q := openDB(t)
	home := t.TempDir()
	e := testEnv(home, clock)
	writeConfig(t, home, map[string]any{"active_cap": 4})
	for acct, used := range map[string]int{"easy": 50, "half": 55, "empty": 60, "ahead": 80} {
		os.MkdirAll(filepath.Join(home, "accounts", acct), 0o755)
		weekAt(t, home, acct, used)
	}
	os.MkdirAll(filepath.Join(home, "accounts", "unread"), 0o755)
	loads, err := e.ReadAll(q, "")
	if err != nil {
		t.Fatal(err)
	}
	for acct, want := range map[string]int{"easy": 4, "half": 2, "empty": 0, "ahead": 0, "unread": 4} {
		if got := loads[acct].Cap; got != want {
			t.Errorf("%s: cap %d, want %d (the configured 4 is the most)", acct, got, want)
		}
	}
	if !loads["ahead"].AtCap() || loads["easy"].AtCap() {
		t.Fatal("an account ahead of its week is at its cap with nobody on it")
	}
	if why, _ := NoRoom(e.Read("ahead"), loads["ahead"], nil, "ann"); why == "" {
		t.Fatal("an account ahead of its week takes no new agent")
	}
	if why, _ := NoRoom(e.Read("easy"), loads["easy"], nil, "ann"); why != "" {
		t.Fatalf("an account on pace still does: %q", why)
	}
	if ok, _ := e.AccountVerdict("ahead", e.Read("ahead"), loads["ahead"], nil, true); ok {
		t.Fatal("the verdict placement acts on refuses it")
	}
	if ok, why := e.AccountVerdict("easy", e.Read("easy"), loads["easy"], nil, true); !ok {
		t.Fatalf("and passes the account on pace: %q", why)
	}
}

func TestPacedCapRoundsUpSoAnyHeadroomKeepsOneSlot(t *testing.T) {
	for _, c := range []struct {
		base int
		h    float64
		want int
	}{{2, 0.2, 2}, {2, PaceBurst, 2}, {2, 0.05, 1}, {2, 0.001, 1}, {2, 0, 0}, {2, -1, 0}, {3, 0.05, 2}} {
		if got := PacedCap(c.base, c.h); got != c.want {
			t.Errorf("PacedCap(%d, %v) = %d, want %d", c.base, c.h, got, c.want)
		}
	}
}
