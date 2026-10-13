package dangling

import (
	"database/sql"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/store"
)

// host is a fake terminal host: the panes it has, the session in each, and the processes that run.
type host struct {
	live    map[string]bool // nil: the host cannot say
	started map[string]int64
	session map[string]int // pane -> the pid of the session in it
	running map[int]int64  // pid -> its start time
	survive bool           // a session goes on when its pane closes
	asked   []int
	closed  []string
	killed  []int
	told    []string
}

func (h *host) Live() map[string]bool { return h.live }
func (h *host) PaneStarted(handle string) int64 {
	if s, ok := h.started[handle]; ok {
		return s
	}
	return 900
}
func (h *host) PaneSession(handle string) (int, bool) { return h.session[handle], true }
func (h *host) Ask(pid int, _ string) error           { h.asked = append(h.asked, pid); return nil }
func (h *host) Close(handle string) (bool, string) {
	h.closed = append(h.closed, handle)
	delete(h.live, handle)
	if pid := h.session[handle]; pid != 0 && !h.survive {
		delete(h.running, pid)
	}
	delete(h.session, handle)
	return true, ""
}
func (h *host) PidGone(pid int) bool { _, ok := h.running[pid]; return !ok }
func (h *host) PidStart(pid int) int64 {
	return h.running[pid]
}
func (h *host) Kill(pid int) error {
	h.killed = append(h.killed, pid)
	delete(h.running, pid)
	return nil
}
func (h *host) Tell(to, text string) error { h.told = append(h.told, to+": "+text); return nil }

type rig struct {
	q    *sql.DB
	h    *host
	s    *Sweep
	home string
}

// newRig: agent a.b was opened in %1 at 1000 and moved to %2 at 2000; both panes stand.
func newRig(t *testing.T) *rig {
	t.Helper()
	q, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { q.Close() })
	was := db.Now
	t.Cleanup(func() { db.Now = was })
	db.Now = func() int64 { return 1000 }
	if err := db.SetTerminal(q, "a.b", "%1", "/wt"); err != nil {
		t.Fatal(err)
	}
	db.Now = func() int64 { return 2000 }
	if err := db.SetTerminal(q, "a.b", "%2", "/wt"); err != nil {
		t.Fatal(err)
	}
	home := t.TempDir()
	h := &host{live: map[string]bool{"%1": true, "%2": true}, started: map[string]int64{}, session: map[string]int{}, running: map[int]int64{}}
	return &rig{q: q, h: h, home: home, s: &Sweep{Q: q, Home: home, Ports: h}}
}

func (r *rig) run(t *testing.T, now int64) {
	t.Helper()
	if err := r.s.Run(now); err != nil {
		t.Fatal(err)
	}
}

func (r *rig) closedAt(t *testing.T, handle string) sql.NullInt64 {
	t.Helper()
	var c sql.NullInt64
	if err := r.q.QueryRow("SELECT closed_at FROM agent_terminals WHERE handle = ?", handle).Scan(&c); err != nil {
		t.Fatal(err)
	}
	return c
}

func TestADanglingPaneWithNoSessionIsClosedAtOnceAndTheManagerIsTold(t *testing.T) {
	r := newRig(t)
	r.run(t, 3000)
	if strings.Join(r.h.closed, ",") != "%1" || !r.closedAt(t, "%1").Valid || r.closedAt(t, "%2").Valid {
		t.Fatalf("only the old pane closes: %v", r.h.closed)
	}
	if len(r.h.told) != 1 || !strings.HasPrefix(r.h.told[0], "a: closed a.b's terminal %1: not its current terminal (%2); no session ran in it") {
		t.Fatalf("the manager hears it: %v", r.h.told)
	}
}

func TestADanglingSessionIsAskedForItsHandoffAndClosedOnceItIsWritten(t *testing.T) {
	r := newRig(t)
	r.h.session["%1"], r.h.running[37735] = 37735, 500
	r.run(t, 3000)
	if len(r.h.asked) != 1 || r.h.asked[0] != 37735 || len(r.h.closed) != 0 {
		t.Fatalf("asked first, not closed: asked %v closed %v", r.h.asked, r.h.closed)
	}
	r.run(t, 3010)
	if len(r.h.asked) != 1 || len(r.h.closed) != 0 {
		t.Fatalf("asked once and waited on: asked %v closed %v", r.h.asked, r.h.closed)
	}
	note := paths.StrayHandoffFile(r.home, "a.b", 37735)
	os.MkdirAll(filepath.Dir(note), 0o755)
	os.WriteFile(note, []byte("where I was"), 0o644)
	r.run(t, 3020)
	if strings.Join(r.h.closed, ",") != "%1" || !r.closedAt(t, "%1").Valid {
		t.Fatalf("closed once the note is in: %v", r.h.closed)
	}
	if len(r.h.told) != 1 || !strings.Contains(r.h.told[0], "pid 37735") || !strings.Contains(r.h.told[0], "written to "+note) {
		t.Fatalf("the manager is pointed at the note: %v", r.h.told)
	}
}

func TestADanglingSessionThatWritesNothingIsClosedAfterTheWait(t *testing.T) {
	r := newRig(t)
	r.h.session["%1"], r.h.running[37735] = 37735, 500
	r.run(t, 3000)
	r.run(t, 3000+AskWaitS-1)
	if len(r.h.closed) != 0 {
		t.Fatal("closed before the wait is over")
	}
	r.run(t, 3000+AskWaitS)
	if strings.Join(r.h.closed, ",") != "%1" || !strings.Contains(r.h.told[0], "wrote none") {
		t.Fatalf("closed after the wait: %v %v", r.h.closed, r.h.told)
	}
}

func TestASessionThatOutlivesItsDanglingPaneIsEndedByItsPidOnlyWhileItIsTheSameProcess(t *testing.T) {
	r := newRig(t)
	r.h.session["%1"], r.h.running[37735], r.h.survive = 37735, 500, true
	r.run(t, 3000)
	r.run(t, 3000+AskWaitS)
	if len(r.h.closed) != 1 || r.closedAt(t, "%1").Valid {
		t.Fatalf("the pane closed and the session is still watched: %v", r.h.closed)
	}
	r.run(t, 3000+AskWaitS+5)
	if len(r.h.killed) != 1 || r.h.killed[0] != 37735 {
		t.Fatalf("the session is ended by its pid: %v", r.h.killed)
	}
	r.run(t, 3000+AskWaitS+10)
	if !r.closedAt(t, "%1").Valid {
		t.Fatal("recorded closed once the pid is gone")
	}

	r = newRig(t)
	r.h.session["%1"], r.h.running[37735], r.h.survive = 37735, 500, true
	r.run(t, 3000)
	r.run(t, 3000+AskWaitS)
	r.h.running[37735] = 999 // the number now belongs to another process
	r.run(t, 3000+AskWaitS+5)
	if len(r.h.killed) != 0 || !r.closedAt(t, "%1").Valid {
		t.Fatalf("a reused pid is never hit: %v", r.h.killed)
	}
}

func TestPanesTheDaemonDidNotOpenForAnAgentAreNeverTouched(t *testing.T) {
	r := newRig(t)
	r.h.live = nil
	r.run(t, 3000)
	if len(r.h.closed) != 0 || r.closedAt(t, "%1").Valid {
		t.Fatal("a host that cannot say is no answer")
	}
	r.h.live = map[string]bool{"%1": true, "%2": true, "%3": true}
	db.RecordTerminal(r.q, "%3", "") // the app's empty terminal
	r.h.started["%1"] = 1000 + OpenSlackS + 1
	r.run(t, 3000)
	if len(r.h.closed) != 0 || len(r.h.told) != 0 {
		t.Fatalf("an empty terminal, the agent's own, and another pane under a reused handle stay: %v", r.h.closed)
	}
	if !r.closedAt(t, "%1").Valid {
		t.Fatal("the stale record of a reused handle is closed")
	}
}

func TestARecordedPaneThatIsGoneIsRecordedClosed(t *testing.T) {
	r := newRig(t)
	delete(r.h.live, "%1")
	r.run(t, 3000)
	if len(r.h.closed) != 0 || len(r.h.told) != 0 || !r.closedAt(t, "%1").Valid {
		t.Fatalf("nothing to close, only the record: %v %v", r.h.closed, r.h.told)
	}
}
