package housekeeping

import (
	"compress/gzip"
	"io"
	"os"
	"path/filepath"
	"strconv"
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
	zf, err := os.Open(p + ".1.gz")
	if err != nil {
		t.Fatal(err)
	}
	defer zf.Close()
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

// listing stands in for /dev/fd with descriptors the test holds open, so the count is the test's own: the process's real count moves
// whenever a finalizer closes a file some earlier test left open. Its directory, open, stands for the listing's own descriptor.
type listing struct {
	t         *testing.T
	dir, held string
}

func newListing(t *testing.T) *listing {
	l := &listing{t: t, dir: t.TempDir(), held: filepath.Join(t.TempDir(), "held")}
	if err := os.WriteFile(l.held, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	l.list(l.dir)
	return l
}

// list opens p and names its descriptor in the listing.
func (l *listing) list(p string) {
	f, err := os.Open(p)
	if err != nil {
		l.t.Fatal(err)
	}
	l.t.Cleanup(func() { f.Close() })
	if err := os.WriteFile(filepath.Join(l.dir, strconv.Itoa(int(f.Fd()))), nil, 0o644); err != nil {
		l.t.Fatal(err)
	}
}

func (l *listing) files(k int) {
	for i := 0; i < k; i++ {
		l.list(l.held)
	}
}

func TestTheDescriptorCountIsLoggedOnFirstReadAndOnBigMovesAndNamedNearTheLimit(t *testing.T) {
	if n := (&FDs{Log: func(string) {}}).Check(); n <= 0 {
		t.Fatalf("the process's own /dev/fd is read: %d", n)
	}
	var lines []string
	l := newListing(t)
	l.files(6)
	f := &FDs{Log: func(s string) { lines = append(lines, s) }, listDir: l.dir, softLimit: func() uint64 { return 1 << 20 }}
	if n := f.Check(); n != 6 || len(lines) != 1 || lines[0] != "  fds: 6/1048576" {
		t.Fatalf("%d %q", n, lines)
	}
	f.Check()
	if len(lines) != 1 {
		t.Fatalf("a steady count is not logged again: %q", lines)
	}
	f.softLimit = func() uint64 { return 7 } // 6 is past 80% of it
	f.Check()
	f.Check()
	l.files(1)
	f.Check()
	want := []string{"  fds: 6/1048576", "  fds: near the limit - file 6, dir 1", "  fds: 7/7"}
	if strings.Join(lines, "|") != strings.Join(want, "|") {
		t.Fatalf("named by kind once per crossing, and a move of a twentieth logged: %q", lines)
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
