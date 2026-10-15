package turns

import (
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

const wait = 5 * time.Second

func tracker(t *testing.T) (*Tracker, chan string) {
	interrupted := make(chan string, 4)
	tr := &Tracker{Agent: func(h string) string {
		if h == "%1" {
			return "amy"
		}
		return ""
	}, Interrupted: func(a string) { interrupted <- a }, Log: t.Logf}
	return tr, interrupted
}

func transcript(t *testing.T, lines ...string) string {
	path := filepath.Join(t.TempDir(), "s.jsonl")
	write(t, path, lines...)
	return path
}

func write(t *testing.T, path string, lines ...string) {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	for _, l := range lines {
		f.WriteString(l + "\n")
	}
}

func idle(tr *Tracker, d time.Duration) (bool, bool) { return tr.WaitIdle("%1", d) }

func TestAPromptOpensATurnAndStopClosesIt(t *testing.T) {
	tr, _ := tracker(t)
	if _, known := idle(tr, 0); known {
		t.Fatal("no hook yet, and the turn was known")
	}
	tr.Hook("amy", "UserPromptSubmit", nil, 10)
	if ok, known := idle(tr, 0); ok || !known {
		t.Fatalf("mid-turn read as idle=%v known=%v", ok, known)
	}
	tr.Hook("amy", "Stop", nil, 11)
	if ok, known := idle(tr, 0); !ok || !known {
		t.Fatalf("after Stop: idle=%v known=%v", ok, known)
	}
	tr.Hook("amy", "UserPromptSubmit", nil, 12)
	tr.Hook("amy", "StopFailure", map[string]any{"error": "rate_limit"}, 13)
	if ok, _ := idle(tr, 0); !ok {
		t.Fatal("StopFailure left the turn open")
	}
	if _, known := tr.WaitIdle("%9", 0); known {
		t.Fatal("a terminal with no agent was known")
	}
}

func TestWaitIdleWakesOnTheStopWithoutPolling(t *testing.T) {
	tr, _ := tracker(t)
	tr.Hook("amy", "UserPromptSubmit", nil, 10)
	go func() { time.Sleep(50 * time.Millisecond); tr.Hook("amy", "Stop", nil, 11) }()
	start := time.Now()
	if ok, _ := idle(tr, wait); !ok || time.Since(start) > wait/2 {
		t.Fatalf("idle=%v after %v", ok, time.Since(start))
	}
	tr.Hook("amy", "UserPromptSubmit", nil, 12)
	if ok, known := idle(tr, 30*time.Millisecond); ok || !known {
		t.Fatalf("a turn that does not end timed out as idle=%v known=%v", ok, known)
	}
}

func TestALateEventAndANewSessionMoveTheTurnRight(t *testing.T) {
	tr, _ := tracker(t)
	tr.Hook("amy", "Stop", nil, 20)
	tr.Hook("amy", "UserPromptSubmit", nil, 19)
	if ok, _ := idle(tr, 0); !ok {
		t.Fatal("a prompt older than the Stop reopened the turn")
	}
	tr.Hook("amy", "SessionStart", nil, 21)
	if _, known := idle(tr, 0); known {
		t.Fatal("a new session kept the old session's turn")
	}
}

func TestEscIsSeenInTheTranscriptAndClosesTheTurn(t *testing.T) {
	for _, entry := range []string{
		`{"type":"user","message":{"role":"user","content":[{"type":"text","text":"[Request interrupted by user]"}]}}`,
		`{"type":"user","message":{"role":"user","content":"[Request interrupted by user for tool use]"}}`,
	} {
		tr, interrupted := tracker(t)
		// An Esc of an earlier turn is already in the file: it does not close this one.
		path := transcript(t, entry)
		tr.Hook("amy", "UserPromptSubmit", map[string]any{"transcript_path": path}, 10)
		write(t, path, `{"type":"user","message":{"role":"user","content":"go on"}}`, `{"type":"assistant","message":{"content":[]}}`)
		if ok, _ := idle(tr, 200*time.Millisecond); ok {
			t.Fatal("a turn with no interrupt in it closed")
		}
		write(t, path, entry)
		if ok, known := idle(tr, wait); !ok || !known {
			t.Fatalf("the Esc did not close the turn: idle=%v known=%v", ok, known)
		}
		select {
		case a := <-interrupted:
			if a != "amy" {
				t.Fatalf("told %q", a)
			}
		case <-time.After(wait):
			t.Fatal("Interrupted was not told")
		}
		if _, known := tr.Background("%1"); known {
			t.Fatal("background work after an Esc read as known")
		}
	}
}

func TestAnEntryWrittenInPiecesIsReadWhole(t *testing.T) {
	tr, _ := tracker(t)
	path := transcript(t)
	tr.Hook("amy", "UserPromptSubmit", map[string]any{"transcript_path": path}, 10)
	f, _ := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0)
	defer f.Close()
	f.WriteString(`{"type":"user","message":{"content":"[Request inter`)
	time.Sleep(50 * time.Millisecond)
	f.WriteString("rupted by user]\"}}\n")
	if ok, _ := idle(tr, wait); !ok {
		t.Fatal("a line written in two parts was not read")
	}
}

func TestStopSaysWhetherBackgroundWorkStillRuns(t *testing.T) {
	tr, _ := tracker(t)
	if _, known := tr.Background("%1"); known {
		t.Fatal("known before any Stop")
	}
	tr.Hook("amy", "UserPromptSubmit", nil, 10)
	tr.Hook("amy", "Stop", map[string]any{"background_tasks": []any{
		map[string]any{"id": "b1", "type": "shell", "status": "running", "description": "sleep 60"},
		map[string]any{"id": "b2", "type": "shell", "status": "completed", "description": "ls"},
	}}, 11)
	if running, known := tr.Background("%1"); running != "1 background task" || !known {
		t.Fatalf("%q %v", running, known)
	}
	// The shell ending while idle: a prompt, then a Stop with nothing left.
	tr.Hook("amy", "UserPromptSubmit", nil, 12)
	if _, known := tr.Background("%1"); known {
		t.Fatal("mid-turn background work read as known")
	}
	tr.Hook("amy", "Stop", map[string]any{"background_tasks": []any{}}, 13)
	if running, known := tr.Background("%1"); running != "" || !known {
		t.Fatalf("done read as %q %v", running, known)
	}
}

func TestAChangeIsToldOnceTheTurnOpensOrClosesAndNotOtherwise(t *testing.T) {
	tr, _ := tracker(t)
	var told []string
	tr.OnChange(func(a string) { told = append(told, a) })
	tr.Hook("amy", "UserPromptSubmit", nil, 10)
	tr.Hook("amy", "UserPromptSubmit", nil, 11)
	tr.Hook("amy", "PostToolUse", nil, 12)
	tr.Hook("amy", "Stop", nil, 13)
	if len(told) != 2 {
		t.Fatalf("told %d times, want 2 (open, close)", len(told))
	}
}

func TestASessionStartIsToldEvenWhenTheTurnStaysUnknown(t *testing.T) {
	tr, _ := tracker(t)
	var told []string
	tr.OnChange(func(a string) { told = append(told, a) })
	tr.Hook("amy", "SessionStart", nil, 10) // Unknown before and after: the view still has a launch to end
	if len(told) != 1 || told[0] != "amy" {
		t.Fatalf("told %v", told)
	}
}

// session starts a process standing in for a CLI; kill ends it as SIGKILL would, its parent reaping it.
func session(t *testing.T) (pid int, kill func()) {
	cmd := exec.Command("sleep", "60")
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	var once sync.Once
	kill = func() { once.Do(func() { cmd.Process.Kill(); cmd.Wait() }) }
	t.Cleanup(kill)
	return cmd.Process.Pid, kill
}

func TestAHardKilledSessionEndsItsTurnAndItsTranscriptWatchAtOnce(t *testing.T) {
	tr, _ := tracker(t)
	pid, kill := session(t)
	ended := make(chan string, 4)
	tr.OnEnd(func(string) int { return pid }, func(a string) { ended <- a })
	tr.Hook("amy", "UserPromptSubmit", map[string]any{"transcript_path": transcript(t)}, 10)
	if ok, known := idle(tr, 0); ok || !known {
		t.Fatalf("mid-turn read as idle=%v known=%v", ok, known)
	}
	start := time.Now()
	kill()
	select {
	case a := <-ended:
		if a != "amy" {
			t.Fatalf("told %q", a)
		}
	case <-time.After(wait):
		t.Fatal("the exit was not told")
	}
	if took := time.Since(start); took > time.Second {
		t.Fatalf("the exit took %v to be told", took)
	}
	if _, known := idle(tr, 0); known {
		t.Fatal("a session that is gone kept its turn")
	}
	tr.mu.Lock()
	watching := tr.get("amy").unwatch != nil
	tr.mu.Unlock()
	if watching {
		t.Fatal("the transcript of a session that is gone is still watched")
	}
}

func TestTheExitOfAReplacedSessionIsNotTold(t *testing.T) {
	tr, _ := tracker(t)
	old, kill := session(t)
	current := old
	var mu sync.Mutex
	ended := make(chan string, 4)
	tr.OnEnd(func(string) int { mu.Lock(); defer mu.Unlock(); return current }, func(a string) { ended <- a })
	tr.Hook("amy", "UserPromptSubmit", nil, 10)
	next, _ := session(t)
	mu.Lock()
	current = next
	mu.Unlock()
	tr.Hook("amy", "SessionStart", nil, 11)
	tr.Hook("amy", "UserPromptSubmit", nil, 12)
	kill()
	select {
	case a := <-ended:
		t.Fatalf("the old session's exit was told for %q", a)
	case <-time.After(300 * time.Millisecond):
	}
	if ok, known := idle(tr, 0); ok || !known {
		t.Fatalf("the new session's turn moved: idle=%v known=%v", ok, known)
	}
}
