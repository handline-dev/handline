package usage

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

// The expected answers were read off the Python cycle module for the same settings (the moments are local midnights).
func TestEndOfAndWarnDaysAgreeWithPython(t *testing.T) {
	cfg := Config{"month_cycle_a": true, "sub_end_a": "2026-10-22", "month_cycle_b": false, "sub_end_b": "2026-10-22", "sub_end_c": "2026-11-30",
		"month_cycle_d": true, "sub_end_d": "garbage", "cycle_warn_days": 5}
	now := time.Date(2026, 10, 8, 12, 0, 0, 0, time.Local)
	for acct, want := range map[string]string{"a": "2026-10-23", "c": "2026-12-01"} {
		got, ok := cfg.EndOf("/nowhere", acct, now)
		d, _ := time.ParseInLocation("2006-01-02", want, time.Local)
		if !ok || !got.Equal(d) {
			t.Errorf("%s: %v %v, want %v", acct, got, ok, d)
		}
	}
	for _, acct := range []string{"b", "d", "z"} {
		if got, ok := cfg.EndOf("/nowhere", acct, now); ok {
			t.Errorf("%s: an end where Python has none: %v", acct, got)
		}
	}
	if cfg.WarnDays() != 5 || (Config{}).WarnDays() != 3 || (Config{"cycle_warn_days": -1}).WarnDays() != 3 || (Config{"cycle_warn_days": true}).WarnDays() != 3 {
		t.Error("warn days")
	}
	if _, ok := DayEnd("not a day"); ok {
		t.Error("a day that is not one")
	}
}

func TestADefaultEndIsTheNextAnniversaryOfTheSubscriptionStart(t *testing.T) {
	home := t.TempDir()
	dir := filepath.Join(home, "accounts", "a", ".claude")
	os.MkdirAll(dir, 0o755)
	os.WriteFile(filepath.Join(dir, ".claude.json"), []byte(`{"oauthAccount": {"subscriptionCreatedAt": "2026-08-31T09:30:00+00:00"}}`), 0o644)
	cfg := Config{"month_cycle_a": true}
	now := time.Date(2026, 10, 8, 12, 0, 0, 0, time.Local)
	got, ok := cfg.EndOf(home, "a", now)
	if !ok || !got.After(now) || got.Sub(now) > 32*24*time.Hour {
		t.Fatalf("%v %v", got, ok)
	}
	if day := cfg.EndDate(home, "a", now); day != got.Format("2006-01-02") {
		t.Errorf("end date %q, end %v", day, got)
	}
}

func TestConfigOrNilTellsAMissingFileFromOneItCannotRead(t *testing.T) {
	home := t.TempDir()
	if c, ok := LoadConfigOrNil(home); !ok || len(c) != 0 {
		t.Errorf("a missing file says nothing: %v %v", c, ok)
	}
	os.WriteFile(filepath.Join(home, "config.yaml"), []byte("a: 1\n"), 0o644)
	if c, ok := LoadConfigOrNil(home); !ok || c["a"] == nil {
		t.Errorf("%v %v", c, ok)
	}
	os.WriteFile(filepath.Join(home, "config.yaml"), []byte("a: [1\n"), 0o644)
	if _, ok := LoadConfigOrNil(home); ok {
		t.Error("a half-written file read as empty")
	}
	os.WriteFile(filepath.Join(home, "config.yaml"), []byte("- a\n- b\n"), 0o644)
	if _, ok := LoadConfigOrNil(home); ok {
		t.Error("a list read as a mapping")
	}
}
