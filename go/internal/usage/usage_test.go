package usage

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestStatusReadsBothShapes(t *testing.T) {
	home := t.TempDir()
	write := func(acct, body string) {
		dir := filepath.Join(home, "accounts", acct)
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "status.json"), []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	write("new", `{"windows":[{"minutes":10080,"used_percent":41.2,"resets_at":9},{"minutes":300,"used_percent":12,"carried":true}]}`)
	write("old", `{"pct_5h":28.999999999999996,"reset_5h":5,"rl_carried":1,"pct_7d":null}`)
	write("bad", `[1]`)
	n := ReadStatus(home, "new")
	if n.Short.Minutes != 300 || *n.Short.Pct != 12 || !n.Short.Carried || *n.Long.Pct != 42 || *n.Long.Reset != 9 {
		t.Fatalf("new shape: %+v", n)
	}
	o := ReadStatus(home, "old")
	if *o.Short.Pct != 29 || !o.Short.Carried || o.Long.Pct != nil {
		t.Fatalf("legacy shape: %+v", o)
	}
	if ReadStatus(home, "bad").Error == "" || ReadStatus(home, "none").Short.Pct != nil {
		t.Fatal("a bad file is an error and a missing one no reading")
	}
}

func TestCapsLabelsAndEnd(t *testing.T) {
	c := Config{"active_cap": int64(3), "active_cap_a": int64(0), "label_a": " ", "sub_end_a": "2026-10-22", "month_cycle_b": true}
	if c.CapOf("a") != 3 || c.LabelOf("a") != "a" || c.CapOf("z") != 3 || (Config{}).DefaultCap() != DefaultActiveCap {
		t.Fatal("caps and labels")
	}
	if got := c.EndDate(t.TempDir(), "a", time.Now()); got != "2026-10-22" {
		t.Fatalf("a typed end counts without the switch: %q", got)
	}
	if got := c.EndDate(t.TempDir(), "b", time.Now()); got != "" {
		t.Fatalf("no profile, no end: %q", got)
	}
	if got := (Config{"month_cycle_a": false, "sub_end_a": "2026-10-22"}).EndDate("", "a", time.Now()); got != "" {
		t.Fatalf("off keeps the date but has no end: %q", got)
	}
}
