package housekeeping

import (
	"compress/gzip"
	"io"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestALogOverTheLimitIsCopiedToABackupAndTruncatedInPlace(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "daemon.out.log")
	os.WriteFile(p, []byte(strings.Repeat("x", 100)), 0o644)
	if ok, err := RotateLog(p, 200, 3); ok || err != nil {
		t.Fatalf("under the limit: %v %v", ok, err)
	}
	f, _ := os.OpenFile(p, os.O_WRONLY|os.O_APPEND, 0) // the writer launchd keeps open
	defer f.Close()
	for i := 0; i < 5; i++ {
		f.WriteString(strings.Repeat("y", 250))
		if ok, err := RotateLog(p, 200, 3); !ok || err != nil {
			t.Fatalf("round %d: %v %v", i, ok, err)
		}
	}
	if st, _ := os.Stat(p); st.Size() != 0 {
		t.Fatalf("truncated in place: %d", st.Size())
	}
	f.WriteString("next")
	if b, _ := os.ReadFile(p); string(b) != "next" {
		t.Fatalf("the open writer lands at the start: %q", b)
	}
	zf, _ := os.Open(p + ".1.gz")
	zr, err := gzip.NewReader(zf)
	if err != nil {
		t.Fatal(err)
	}
	if b, _ := io.ReadAll(zr); !strings.HasPrefix(string(b), "y") {
		t.Fatalf("the newest backup holds the last rotation: %q", b[:5])
	}
	if _, err := os.Stat(p + ".4.gz"); err == nil {
		t.Fatal("more backups than the limit")
	}
	if _, err := os.Stat(p + ".3.gz"); err != nil {
		t.Fatal("the oldest kept backup is there")
	}
}

func TestTheDescriptorCountIsLoggedOnFirstReadAndOnBigMovesAndNamedNearTheLimit(t *testing.T) {
	var lines []string
	f := &FDs{Log: func(s string) { lines = append(lines, s) }, softLimit: func() uint64 { return 1 << 20 }}
	n := f.Check()
	if n <= 0 || len(lines) != 1 || !strings.HasPrefix(lines[0], "  fds: ") {
		t.Fatalf("%d %q", n, lines)
	}
	f.Check()
	if len(lines) != 1 {
		t.Fatalf("a steady count is not logged again: %q", lines)
	}
	f.softLimit = func() uint64 { return uint64(n + 1) } // past 80% of a limit this small
	f.Check()
	f.Check()
	near := 0
	for _, l := range lines {
		if strings.Contains(l, "near the limit") {
			near++
			if !strings.Contains(l, "file") && !strings.Contains(l, "char") {
				t.Fatalf("the inventory names kinds: %q", l)
			}
		}
	}
	if near != 1 {
		t.Fatalf("named once per crossing, not %d times: %q", near, lines)
	}
}

func TestExhaustionIsSaidAtMostOnceAMinute(t *testing.T) {
	var lines []string
	now := time.Unix(1000, 0)
	f := &FDs{Log: func(s string) { lines = append(lines, s) }, Now: func() time.Time { return now }}
	f.SayExhaustion(syscall.EMFILE, "accepting")
	f.SayExhaustion(syscall.EMFILE, "accepting")
	now = now.Add(61 * time.Second)
	f.SayExhaustion(syscall.EMFILE, "accepting")
	if len(lines) != 2 || !IsExhaustion(syscall.EMFILE) || IsExhaustion(syscall.ENOENT) {
		t.Fatalf("%q", lines)
	}
}
