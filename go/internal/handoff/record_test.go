package handoff

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
)

func TestTheRequestLineNamesTheDeliveredHeadOrSaysNone(t *testing.T) {
	w := newWorld(t)
	if got, _ := w.RequestLine("a"); got != "request: none in progress" {
		t.Errorf("no head: %q", got)
	}
	w.heads.heads["a"] = &db.Request{ID: 4, Requester: "b", Body: "x"} // told to nobody yet
	if got, _ := w.RequestLine("a"); got != "request: none in progress" {
		t.Errorf("an undelivered head: %q", got)
	}
	w.heads.heads["a"] = told(4, "b", "first line of it\nsecond\n"+strings.Repeat("x", 300))
	if got, _ := w.RequestLine("a"); got != "request: #4 from b - first line of it" {
		t.Errorf("got %q", got)
	}
	w.heads.heads["a"] = told(4, "b", strings.Repeat("é", 200))
	if got, _ := w.RequestLine("a"); got != "request: #4 from b - "+strings.Repeat("é", 120) {
		t.Errorf("a long line is cut at 120 characters: %q", got)
	}
}

func TestProgressIsKeptOnTheRequestThenTheTaskThenTheAgent(t *testing.T) {
	w := newWorld(t)
	w.agent("a", "IDLE", "", "")
	w.heads.heads["a"] = told(9, "b", "do it")
	ok, where, err := w.SetProgress("a", "  done: x\x1b[31m  ")
	if !ok || err != nil || where != "#9" {
		t.Fatalf("%v %q %v", ok, where, err)
	}
	if got, _ := w.GetProgress("a"); got != "done: x[31m" && got != "done: x" {
		t.Errorf("kept %q", got)
	}
	if got, _ := w.ProgressOf(9); got == "" {
		t.Error("ProgressOf misses the note on the request")
	}
	if ok, _, _ := w.SetProgress("a", ""); !ok {
		t.Fatal("clearing")
	}
	if got, _ := w.GetProgress("a"); got != "" {
		t.Errorf("an empty note did not clear: %q", got)
	}

	delete(w.heads.heads, "a")
	if _, where, _ := w.SetProgress("a", "on the agent"); where != "you (no request in progress)" {
		t.Errorf("where %q", where)
	}
	if got, _ := w.GetProgress("a"); got != "on the agent" {
		t.Errorf("got %q", got)
	}
	w.exec("INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(12, 't', 'a', 'b', 1)")
	w.exec("UPDATE agent_state SET current_task = 12")
	if _, where, _ := w.SetProgress("a", "on the task"); where != "T12 (no request in progress)" {
		t.Errorf("where %q", where)
	}
	if got, _ := w.GetProgress("a"); got != "on the task" {
		t.Errorf("got %q", got)
	}

	ok, msg, _ := w.SetProgress("a", strings.Repeat("é", ProgressMax+1))
	if ok || msg != "1501 characters; the handoff carries at most 1500 - keep what is done and what is left" {
		t.Errorf("too long: %v %q", ok, msg)
	}
	if ok, _, _ := w.SetProgress("a", strings.Repeat("é", ProgressMax)); !ok {
		t.Error("exactly the most is refused")
	}
}

func TestATranscriptIsFoundByItsIdUnderTheAccountThenTheOthers(t *testing.T) {
	w := newWorld(t)
	other := w.transcript("beta", "-proj-b", "sess-1")
	mine := w.transcript("alpha", "-proj-a", "sess-1")
	if got := w.FindTranscript("sess-1", "alpha"); got != mine {
		t.Errorf("own account first: %q", got)
	}
	if got := w.FindTranscript("sess-1", ""); got != mine && got != other {
		t.Errorf("any account: %q", got)
	}
	if got := w.FindTranscript("sess-1", "beta"); got != other {
		t.Errorf("beta first: %q", got)
	}
	for _, bad := range []string{"", "../x", "-lead", "a b", "nope"} {
		if got := w.FindTranscript(bad, "alpha"); got != "" {
			t.Errorf("%q found %q", bad, got)
		}
	}
	os.WriteFile(filepath.Join(w.Home, "accounts", "alpha", "vendor"), []byte("opencode"), 0o644)
	if got := w.FindTranscript("sess-1", "alpha"); got != other {
		t.Errorf("opencode has no transcript files: %q", got)
	}
	os.WriteFile(filepath.Join(w.Home, "accounts", "alpha", "vendor"), []byte("nonesuch"), 0o644)
	if got := w.FindTranscript("sess-1", "alpha"); got != other {
		t.Errorf("an unknown vendor is skipped: %q", got)
	}
}

func TestThePreviousSessionComesFromTheSnapshotThenTheEventsThenTheName(t *testing.T) {
	w := newWorld(t)
	w.agent("a", "IDLE", "alpha", "")
	found := w.transcript("alpha", "-p", "from-events")
	w.event("a", "SessionStart", `{"session_id": "fresh", "transcript_path": "/fresh"}`) // never talked to
	w.event("a", "Stop", `{"session_id": "from-events"}`)

	w.status["a"] = [2]string{"snap", "/snap/path"}
	if s, p, _ := w.PreviousSession("a", "alpha", ""); s != "snap" || p != "/snap/path" {
		t.Errorf("snapshot: %q %q", s, p)
	}
	w.status["a"] = [2]string{}
	if s, p, _ := w.PreviousSession("a", "alpha", ""); s != "from-events" || p != found {
		t.Errorf("events then the name: %q %q", s, p)
	}
	w.event("a", "UserPromptSubmit", `{"session_id": "from-events", "transcript_path": "/event/path"}`)
	if _, p, _ := w.PreviousSession("a", "alpha", ""); p != "/event/path" {
		t.Errorf("the event's own path: %q", p)
	}
	w.status["a"] = [2]string{"snap", ""}
	if s, p, _ := w.PreviousSession("a", "alpha", ""); s != "snap" || p != "" {
		t.Errorf("a snapshot session is not given another session's path: %q %q", s, p)
	}
	if s, p, _ := w.PreviousSession("a", "alpha", "from-events"); s != "from-events" || p != found {
		t.Errorf("a closed session is looked up by name: %q %q", s, p)
	}
}

func TestTheWorktreeIsReadFromGit(t *testing.T) {
	repo := gitRepo(t)
	lines, gap := worktreeLines(repo)
	if gap != "" || len(lines) != 3 || lines[0] != "worktree: "+repo || lines[1][:len("branch: work @ ")] != "branch: work @ " || lines[2] != "git status: clean" {
		t.Fatalf("%q %q", lines, gap)
	}
	os.WriteFile(filepath.Join(repo, "new.txt"), []byte("x"), 0o644)
	lines, _ = worktreeLines(repo)
	if len(lines) != 4 || lines[2] != "git status:" || lines[3] != "  ?? new.txt" {
		t.Errorf("%q", lines)
	}
	for i := 0; i < 25; i++ {
		os.WriteFile(filepath.Join(repo, "f"+string(rune('a'+i))+".txt"), nil, 0o644)
	}
	lines, _ = worktreeLines(repo)
	if got := lines[len(lines)-1]; got != "  ... and 6 more" || len(lines) != 3+20+1 {
		t.Errorf("a long status is cut: %d lines, last %q", len(lines), got)
	}
	if lines, gap := worktreeLines(""); lines != nil || gap != "the worktree (none recorded, or gone)" {
		t.Errorf("%q %q", lines, gap)
	}
	if _, gap := worktreeLines(t.TempDir()); gap != "the worktree's branch, HEAD or git status" {
		t.Errorf("not a repository: %q", gap)
	}
}

func TestWorktreePathTakesTheDirectoryOutOfASelector(t *testing.T) {
	dir := t.TempDir()
	if WorktreePath(dir) != dir || WorktreePath("repo-id::"+dir) != dir {
		t.Error("a plain path or a selector")
	}
	if WorktreePath("") != "" || WorktreePath(filepath.Join(dir, "gone")) != "" || WorktreePath("a::b::"+dir) != "" {
		t.Error("none, gone, or split at the first ::")
	}
}

func TestCaptureIsTheFactsAndNamesWhatItCouldNotRead(t *testing.T) {
	w := newWorld(t)
	repo := gitRepo(t)
	w.agent("a", "IDLE", "alpha", "repo-id::"+repo)
	w.heads.heads["a"] = told(4, "b", "do the thing")
	w.SetProgress("a", "done: x\nleft: y")
	w.status["a"] = [2]string{"s1", "/t/s1.jsonl"}
	w.MemoryLine = func(string) string { return "memory: over budget" }

	text, missing, err := w.Capture("a", "")
	if err != nil || len(missing) != 0 {
		t.Fatalf("%v %q", err, missing)
	}
	lines := strings.Split(text, "\n")
	want := []string{"# handoff · a", "captured: ", "request: #4 from b - do the thing", "progress (written by the agent):", "  done: x", "  left: y",
		"memory: over budget", "previous session: s1, recorded at /t/s1.jsonl", "Grep it for what you need - do not read it whole, it is a JSONL transcript with every tool result in it.",
		"worktree: " + repo}
	for i, wl := range want {
		if i == 1 {
			if !strings.HasPrefix(lines[1], "captured: 20") {
				t.Errorf("line 1: %q", lines[1])
			}
			continue
		}
		if lines[i] != wl {
			t.Errorf("line %d: %q, want %q", i, lines[i], wl)
		}
	}
	if last := lines[len(lines)-1]; last != "git status: clean" {
		t.Errorf("last line %q", last)
	}

	w.status["a"] = [2]string{}
	w.exec("UPDATE agent_state SET worktree = NULL")
	text, missing, _ = w.Capture("a", "")
	if len(missing) != 2 || missing[0] != "the session's transcript path" || missing[1] != "the worktree (none recorded, or gone)" ||
		!strings.Contains(text, "(could not read the session's transcript path)\n(could not read the worktree (none recorded, or gone))") {
		t.Errorf("%q\n%s", missing, text)
	}
}

func TestRecordKeepsTheRecordWithTheNoteAndClearsTheAsk(t *testing.T) {
	w := newWorld(t)
	w.agent("a", "IDLE", "alpha", "")
	w.telling.accept = true
	w.event("a", "Stop", "")
	if asked, err := w.Ask("a", "close"); !asked || err != nil {
		t.Fatalf("ask: %v %v", asked, err)
	}
	os.MkdirAll(filepath.Dir(w.NotePath("a")), 0o755)
	os.WriteFile(w.NotePath("a"), []byte("deciding: X\n"), 0o644)

	missing, err := w.Record("a", "")
	if err != nil || len(missing) != 2 {
		t.Fatalf("%v %q", err, missing)
	}
	got, _ := db.GetHandoff(w.DB, "a")
	if !strings.Contains(got, "written by the agent just before the close:\n  deciding: X") {
		t.Errorf("the note is not in the record:\n%s", got)
	}
	if _, err := os.Stat(w.NotePath("a")); err == nil {
		t.Error("the note file stayed")
	}
	if _, _, asked, _ := w.Asked("a"); asked {
		t.Error("the ask stayed")
	}
}
