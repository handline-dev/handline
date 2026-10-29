package turns

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
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
	if got := tr.Running("amy"); len(got) != 1 || got[0] != "shell: sleep 60" {
		t.Fatalf("named %q", got)
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
	if got := tr.Running("amy"); got != nil {
		t.Fatalf("nothing left named %q", got)
	}
}

func TestOnlyAStopWithNoPromptSinceSettlesTheTurn(t *testing.T) {
	tr, _ := tracker(t)
	at := int64(10)
	hook := func(event string, raw map[string]any) { tr.Hook("amy", event, raw, at); at++ }
	cases := []struct {
		events  []string
		raw     map[string]any
		settled bool
	}{
		{[]string{"UserPromptSubmit"}, nil, false},
		{[]string{"UserPromptSubmit", "Stop"}, nil, true},
		{[]string{"UserPromptSubmit", "StopFailure"}, nil, false},
		{[]string{"UserPromptSubmit", "Stop", "PermissionRequest"}, nil, false},
		{[]string{"UserPromptSubmit", "Stop", "Notification"}, map[string]any{"notification_type": "permission_prompt"}, false},
		{[]string{"UserPromptSubmit", "Stop", "Notification"}, map[string]any{"notification_type": "idle_prompt"}, true},
		{[]string{"UserPromptSubmit", "Stop", "SessionEnd"}, nil, false},
	}
	for _, c := range cases {
		for _, e := range c.events {
			hook(e, c.raw)
		}
		if got := tr.Settled("%1"); got != c.settled {
			t.Fatalf("%v: settled %v", c.events, got)
		}
	}
	if tr.Settled("%9") {
		t.Fatal("a terminal of no agent read as settled")
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

// childFds is the descriptors a child the daemon starts holds open.
func childFds(t *testing.T) []string {
	out, err := exec.Command("ls", "/dev/fd").Output()
	if err != nil {
		t.Fatal(err)
	}
	return strings.Fields(string(out))
}

func TestAChildStartedWhileWatchesAreOpenInheritsNoneOfTheirDescriptors(t *testing.T) {
	before := childFds(t)
	pid, _ := session(t)
	stopExit, err := watchExit(pid, func() {})
	if err != nil {
		t.Fatal(err)
	}
	defer stopExit()
	stopFile, err := watchFile(transcript(t), func() {}, func() {})
	if err != nil {
		t.Fatal(err)
	}
	defer stopFile()
	if after := childFds(t); len(after) != len(before) {
		t.Fatalf("a child holds %v with the watches open, %v without", after, before)
	}
}

func TestAnExitedSessionNotYetReapedIsNotWatched(t *testing.T) {
	cmd := exec.Command("true")
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer cmd.Wait()
	for deadline := time.Now().Add(wait); !zombie(cmd.Process.Pid); time.Sleep(10 * time.Millisecond) {
		if time.Now().After(deadline) {
			t.Fatal("the process never became a zombie")
		}
	}
	tr, _ := tracker(t)
	tr.reapWait = time.Nanosecond // a watched zombie would be told at once
	ended := make(chan string, 1)
	tr.OnEnd(func(string) int { return cmd.Process.Pid }, func(a string) { ended <- a })
	tr.Hook("amy", "UserPromptSubmit", nil, 10)
	select {
	case <-ended:
		t.Fatal("the exit of the CLI a new session replaced ended the new session's turn")
	case <-time.After(300 * time.Millisecond):
	}
	if ok, known := idle(tr, 0); ok || !known {
		t.Fatalf("the turn moved: idle=%v known=%v", ok, known)
	}
}

func TestAnEndedSessionIsKnownGoneUntilALaunchOrANewStart(t *testing.T) {
	tr, _ := tracker(t)
	if _, known := tr.Session("%1"); known {
		t.Fatal("a session no hook spoke of is known")
	}
	first, kill := session(t)
	current := first
	var mu sync.Mutex
	ended := make(chan string, 4)
	tr.OnEnd(func(string) int { mu.Lock(); defer mu.Unlock(); return current }, func(a string) { ended <- a })
	tr.Hook("amy", "SessionStart", nil, 10)
	if running, known := tr.Session("%1"); !running || !known {
		t.Fatalf("a watched session process: running=%v known=%v", running, known)
	}
	kill()
	select {
	case <-ended:
	case <-time.After(wait):
		t.Fatal("the exit was not told")
	}
	if running, known := tr.Session("%1"); running || !known {
		t.Fatalf("after its process exited: running=%v known=%v", running, known)
	}
	tr.Launched("amy")
	if _, known := tr.Session("%1"); known {
		t.Fatal("a launch left the old session's end standing")
	}
	next, _ := session(t)
	mu.Lock()
	current = next
	mu.Unlock()
	tr.Hook("amy", "SessionStart", nil, 20)
	tr.Hook("amy", "SessionEnd", nil, 21) // /clear: the process lives on
	if running, known := tr.Session("%1"); !running || !known {
		t.Fatalf("after SessionEnd, its process still there: running=%v known=%v", running, known)
	}
}

// With no process watched, a hook after SessionEnd says a session runs again, even when its SessionStart comes late and is dropped.
func TestAHookAfterSessionEndUndoesTheEnd(t *testing.T) {
	tr, _ := tracker(t)
	tr.Hook("amy", "SessionEnd", map[string]any{"reason": "clear"}, 11)
	if running, known := tr.Session("%1"); running || !known {
		t.Fatalf("after SessionEnd: running=%v known=%v", running, known)
	}
	tr.Hook("amy", "UserPromptSubmit", nil, 12)
	tr.Hook("amy", "Stop", nil, 13)
	tr.Hook("amy", "SessionStart", nil, 11) // spooled, replayed late
	if _, known := tr.Session("%1"); known {
		t.Fatal("a session taking turns still reads as ended")
	}
}

// The old process's exit after a launch does not end the new session.
func TestALateExitOfTheOldProcessDoesNotEndANewLaunch(t *testing.T) {
	tr, _ := tracker(t)
	old, kill := session(t)
	ended := make(chan string, 4)
	tr.OnEnd(func(string) int { return old }, func(a string) { ended <- a })
	tr.Hook("amy", "SessionStart", nil, 10)
	tr.Launched("amy")
	kill()
	select {
	case <-ended:
		t.Fatal("the old process's exit was told as the new session's end")
	case <-time.After(300 * time.Millisecond):
	}
	if _, known := tr.Session("%1"); known {
		t.Fatal("the old process's exit ended the new launch")
	}
}

func call(tool, input string) string {
	return `{"type":"assistant","timestamp":"2026-10-23T05:00:00.000Z","message":{"content":[{"type":"text","text":"ok"},{"type":"tool_use","name":"` +
		tool + `","input":` + input + `}]}}`
}

func TestTheRunningTurnsNewestToolCallIsShownAtMostSoOftenAndGoneWhenItEnds(t *testing.T) {
	tr, _ := tracker(t)
	now := time.Unix(1792731600, 0)
	tr.now = func() time.Time { return now }
	var held []func()
	tr.after = func(d time.Duration, f func()) func() bool {
		if d != doingEvery {
			t.Errorf("held for %v", d)
		}
		held = append(held, f)
		return func() bool { return true }
	}
	told := make(chan struct{}, 8)
	tr.OnChange(func(string) { told <- struct{}{} })
	path := transcript(t, call("Read", `{"file_path":"/before/this/turn"}`))
	tr.Hook("amy", "UserPromptSubmit", map[string]any{"transcript_path": path}, 10)
	<-told
	if d := tr.Doing("amy"); d != nil {
		t.Fatalf("a turn that called nothing yet: %v", d)
	}
	long := strings.Repeat("é", 70)
	write(t, path, call("Bash", `{"command":"`+long+`\necho done"}`))
	select {
	case <-told:
	case <-time.After(wait):
		t.Fatal("the first call was not told")
	}
	if d := tr.Doing("amy"); d == nil || d.Text != "Bash "+strings.Repeat("é", 55) || d.At != 1792731600 {
		t.Fatalf("the first line, cut to 60 code points: %+v", d)
	}

	tr.called("amy", &Doing{Text: "Grep x"})
	tr.called("amy", &Doing{Text: "Read y"})
	if d := tr.Doing("amy"); len(held) != 1 || !strings.HasPrefix(d.Text, "Bash ") {
		t.Fatalf("shown again before doingEvery passed: %+v, %d held", d, len(held))
	}
	held[0]()
	<-told
	if d := tr.Doing("amy"); d.Text != "Read y" {
		t.Fatalf("not the newest: %+v", d)
	}

	tr.called("amy", &Doing{Text: "Edit z"})
	tr.Hook("amy", "Stop", map[string]any{}, 20)
	<-told
	if d := tr.Doing("amy"); d != nil {
		t.Fatalf("still doing after the turn ended: %+v", d)
	}
	tr.Hook("amy", "UserPromptSubmit", map[string]any{"transcript_path": path}, 30)
	<-told
	if d := tr.Doing("amy"); d != nil {
		t.Fatalf("the next turn shows the last one's call: %+v", d)
	}
	tr.called("amy", &Doing{Text: "Write w"})
	held[1]()
	if d := tr.Doing("amy"); d != nil {
		t.Fatalf("the last turn's release showed: %+v", d)
	}
	select {
	case <-told:
		t.Fatal("the last turn's release was told")
	default:
	}
	held[2]()
	<-told
	if d := tr.Doing("amy"); d.Text != "Write w" {
		t.Fatalf("got %+v", d)
	}
}

// handline.architect (10-27): the hook named the transcript a second before the session created it, and the watch gave up.
func TestATranscriptThatAppearsAfterThePromptIsWatchedOnceItIsThere(t *testing.T) {
	esc := `{"type":"user","message":{"role":"user","content":"[Request interrupted by user]"}}`
	for _, stopped := range []bool{false, true} {
		tr, interrupted := tracker(t)
		tr.retryWait = 10 * time.Millisecond
		path := filepath.Join(t.TempDir(), "late.jsonl")
		tr.Hook("amy", "UserPromptSubmit", map[string]any{"transcript_path": path}, 10)
		if stopped {
			tr.Hook("amy", "Stop", nil, 11)
			tr.Hook("amy", "UserPromptSubmit", nil, 12)
		}
		write(t, path)
		time.Sleep(200 * time.Millisecond)
		write(t, path, esc)
		select {
		case <-interrupted:
			if stopped {
				t.Fatal("a watch that was stopped before the file appeared still saw it")
			}
		case <-time.After(time.Second):
			if !stopped {
				t.Fatal("the transcript that appeared later was never watched")
			}
		}
		tr.Hook("amy", "SessionEnd", nil, 20) // every watch the test started is stopped
	}
}
