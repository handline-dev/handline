package housekeeping

import (
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/store"
)

const (
	wt      = "/Users/u/work/app.worktrees/dev"
	scratch = "/private/tmp/claude-501"
)

// world is a sweep over a fake process list; stopped records the pids it was asked to stop.
type world struct {
	o       *Orphans
	procs   []Proc
	cwds    map[int]string
	stopped []int
	mode    string
	events  func(kind string) int
	run     func(now int64)
}

func newWorld(t *testing.T) *world {
	t.Helper()
	d, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	w := &world{cwds: map[int]string{}, mode: OrphansOn}
	w.o = &Orphans{Mode: func() string { return w.mode }, Log: func(string) {}, Scratch: scratch,
		List: func() ([]Proc, error) { return w.procs, nil },
		Cwd:  func(pid int) string { return w.cwds[pid] },
		Stop: func(pid int) error { w.stopped = append(w.stopped, pid); return nil }}
	w.events = func(kind string) int {
		var n int
		d.QueryRow("SELECT COUNT(*) FROM events WHERE kind = ?", kind).Scan(&n)
		return n
	}
	w.run = func(now int64) {
		t.Helper()
		if err := w.o.Check(d, now); err != nil {
			t.Fatal(err)
		}
	}
	return w
}

func old(pid int, cmd string) Proc { return Proc{PID: pid, PPID: 1, Age: 2 * time.Hour, Command: cmd} }

func TestAnOrphanedYesOlderThanThirtyMinutesInAWorktreeIsStoppedAndRecorded(t *testing.T) {
	w := newWorld(t)
	w.procs = []Proc{old(500, "yes")}
	w.cwds[500] = wt
	w.run(1000)
	if len(w.stopped) != 1 || w.stopped[0] != 500 || w.events("orphan_stopped") != 1 {
		t.Fatalf("%v %d", w.stopped, w.events("orphan_stopped"))
	}
}

func TestNothingElseIsStopped(t *testing.T) {
	w := newWorld(t)
	w.procs = []Proc{
		{PID: 501, PPID: 1, Age: 10 * time.Minute, Command: "yes"},       // too young
		old(502, "/Applications/Slack.app/Contents/MacOS/Slack"),         // a user app
		old(503, "/Applications/Electron.app/Contents/MacOS/Electron ."), // ours by name, but not in a worktree
		{PID: 504, PPID: 4000, Age: 2 * time.Hour, Command: "yes"},       // has a parent
		old(505, "yes"), // cwd elsewhere
		old(506, "node /x/node_modules/.bin/vitest run"), // cwd unknown
		old(1, "yes"), // init itself
		old(507, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/Users/u/Library/x"), // a person's Chrome
	}
	for _, pid := range []int{501, 502, 504} {
		w.cwds[pid] = wt
	}
	w.cwds[503], w.cwds[505] = "/Users/u/Documents", "/Users/u/Documents"
	w.cwds[507] = wt
	w.run(1000)
	if len(w.stopped) != 0 || w.events("orphan_stopped") != 0 {
		t.Fatalf("stopped %v", w.stopped)
	}
}

func TestEachKindOfOurWorkIsRecognisedInAWorktree(t *testing.T) {
	for kind, cmd := range map[string]string{
		"yes":             "/usr/bin/yes",
		"headless Chrome": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --headless=new --user-data-dir=/tmp/x --remote-debugging-port=0",
		"vitest":          "node /w/node_modules/vitest/vitest.mjs run",
		"vite":            "node /w/node_modules/.bin/vite --port 5173",
		"go test binary":  "/var/folders/ab/T/go-build123/b001/compose.test -test.v",
		"Electron":        "/w/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron .",
	} {
		if got := Ours(old(600, cmd), wt+"/apps/web", scratch); got != kind {
			t.Errorf("%q: %q, want %q", cmd, got, kind)
		}
	}
}

func TestHeadlessChromeWithItsProfileInAWorktreeOrTheScratchRootIsOursWhateverItsCwd(t *testing.T) {
	chrome := func(dir string) Proc { return old(700, "Google Chrome --headless --user-data-dir="+dir+" about:blank") }
	if Ours(chrome(scratch+"/session/chrome-profile"), "/", scratch) == "" || Ours(chrome(wt+"/.tmp/profile"), "", scratch) == "" {
		t.Fatal("a profile under the scratch root or a worktree")
	}
	if Ours(chrome("/Users/u/Library/Application Support/Google/Chrome"), "/", scratch) != "" {
		t.Fatal("a profile elsewhere")
	}
}

func TestDryModeOnlyRecordsWhatItWouldStopAndOffDoesNothing(t *testing.T) {
	w := newWorld(t)
	w.procs = []Proc{old(800, "yes")}
	w.cwds[800] = wt
	w.mode = OrphansDry
	w.run(1000)
	if len(w.stopped) != 0 || w.events("orphan_would_stop") != 1 || w.events("orphan_stopped") != 0 {
		t.Fatalf("dry: %v", w.stopped)
	}
	w.mode = OrphansOff
	w.run(1000 + OrphanEvery)
	if len(w.stopped) != 0 || w.events("orphan_would_stop") != 1 {
		t.Fatal("off looked")
	}
}

func TestTheSweepLooksOnceEveryTenMinutes(t *testing.T) {
	w := newWorld(t)
	w.procs = []Proc{old(900, "yes")}
	w.cwds[900] = wt
	w.run(1000)
	w.run(1000 + OrphanEvery - 1)
	if len(w.stopped) != 1 {
		t.Fatalf("looked again early: %v", w.stopped)
	}
	w.run(1000 + OrphanEvery)
	if len(w.stopped) != 2 {
		t.Fatalf("did not look when due: %v", w.stopped)
	}
}

func TestTheModeIsDryUnlessOnOrOff(t *testing.T) {
	for in, want := range map[any]string{nil: OrphansDry, "on": OrphansOn, "off": OrphansOff, "dry": OrphansDry, "yes": OrphansDry, 3: OrphansDry} {
		if got := OrphansModeOf(map[string]any{OrphansKey: in}); got != want {
			t.Errorf("%v: %q, want %q", in, got, want)
		}
	}
}

func TestPSOutputIsReadWithItsElapsedTimes(t *testing.T) {
	got := parsePS(strings.Join([]string{
		"    1     0 12-03:04:05 /sbin/launchd",
		"  500     1    31:00 yes",
		"  501   500       05 bad",
		"  502     1 02:00:00 node /w/node_modules/.bin/vite --port 1",
	}, "\n"))
	if len(got) != 3 || got[1].Age != 31*time.Minute || got[2].Age != 2*time.Hour || got[0].Age != 12*24*time.Hour+3*time.Hour+4*time.Minute+5*time.Second ||
		got[2].Command != "node /w/node_modules/.bin/vite --port 1" {
		t.Fatalf("%+v", got)
	}
}
