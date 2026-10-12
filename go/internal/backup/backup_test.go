package backup

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestTheGateWantsASnapshotOrACheckFromTheLastFiveMinutes(t *testing.T) {
	home := filepath.Join(t.TempDir(), "orca")
	now := time.Now()
	if got := RequireRecent(home, now); !strings.Contains(got, "there is no backup yet") || !strings.Contains(got, "`hl backup`") {
		t.Fatalf("%q", got)
	}
	if got := Describe(home, now); !strings.HasPrefix(got, "newest backup: none in "+home+"-backups") {
		t.Fatalf("%q", got)
	}
	os.MkdirAll(Dir(home), 0o755)
	snap := filepath.Join(Dir(home), "orcapipe-20260101-000000.tar.gz")
	os.WriteFile(snap, nil, 0o644)
	set := func(p string, ago time.Duration) {
		os.Chtimes(p, now.Add(-ago), now.Add(-ago))
	}
	set(snap, time.Hour)
	if got := RequireRecent(home, now); !strings.Contains(got, "orcapipe-20260101-000000.tar.gz, was taken 60m ago") {
		t.Fatalf("%q", got)
	}
	// nothing changed since: a check a minute ago counts for the snapshot
	fp := filepath.Join(Dir(home), fingerprint)
	os.WriteFile(fp, nil, 0o644)
	set(fp, time.Minute)
	if got := RequireRecent(home, now); got != "" {
		t.Fatalf("%q", got)
	}
	if got := Describe(home, now); !strings.Contains(got, "; checked unchanged 60s ago") {
		t.Fatalf("%q", got)
	}
	set(fp, 6*time.Minute)
	if RequireRecent(home, now) == "" {
		t.Fatal("a check six minutes ago is too old")
	}
	set(snap, 4*time.Minute)
	if got := RequireRecent(home, now); got != "" {
		t.Fatalf("%q", got)
	}
}
