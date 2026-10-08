package session

import (
	"os"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/term"
)

func TestSummaryPicksTheFirstFieldThatSaysSomething(t *testing.T) {
	cases := []struct {
		in   any
		want string
	}{
		{map[string]any{"command": "ls -l", "description": "list"}, "ls -l"},
		{map[string]any{"command": "  ", "file_path": "/a/b"}, "/a/b"},
		{map[string]any{"zeta": 1, "alpha": "x"}, `{"alpha": "x", "zeta": 1}`},
		{map[string]any{"command": "a\x1b[31mb\nc"}, "a [31mb c"},
		{nil, ""},
		{"plain", "plain"},
		{strings.Repeat("é", 200), strings.Repeat("é", 120)},
	}
	for _, c := range cases {
		if got := Summary(c.in); got != c.want {
			t.Errorf("Summary(%v) = %q, want %q", c.in, got, c.want)
		}
	}
}

func TestWaitReasonIsTheDescriptionBesideACommand(t *testing.T) {
	if got := WaitReason(map[string]any{"command": "ls", "description": "list files"}); got != "list files" {
		t.Errorf("got %q", got)
	}
	if got := WaitReason(map[string]any{"description": "list files"}); got != "" {
		t.Errorf("a description without a command: %q", got)
	}
	if got := WaitReason(map[string]any{"command": "ls"}); got != "" {
		t.Errorf("no description: %q", got)
	}
}

func TestANotedWaitIsReadBackAndClearedOnlyByItsOwnCall(t *testing.T) {
	f := newFixture(t)
	id := int64(7)
	in := map[string]any{"command": "make", "description": "build"}
	if err := f.NoteWait("worker", "Bash", in, &id); err != nil {
		t.Fatal(err)
	}
	w := f.ReadWait("worker")
	if w == nil || w.Tool != "Bash" || w.Command != "make" || w.Reason != "build" || w.Since != f.now || w.AfterEventID == nil || *w.AfterEventID != 7 {
		t.Fatalf("read back %+v", w)
	}
	f.ClearWaitFor("worker", "Bash", map[string]any{"command": "other"})
	f.ClearWaitFor("worker", "Edit", in)
	if f.ReadWait("worker") == nil {
		t.Fatal("another call's finish cleared the wait")
	}
	f.ClearWaitFor("worker", "Bash", in)
	if f.ReadWait("worker") != nil {
		t.Fatal("its own call did not clear it")
	}
	f.ClearWaitFor("worker", "Bash", in) // nothing there: nothing happens
	if err := f.NoteWait("worker", "AskUserQuestion", map[string]any{"questions": []any{}}, nil); err != nil {
		t.Fatal(err)
	}
	if w := f.ReadWait("worker"); w == nil || w.QuestionData == nil || w.AfterEventID != nil {
		t.Fatalf("AskUserQuestion wait %+v", w)
	}
	os.WriteFile(f.waitFile("worker"), []byte(`{"tool":"x","since":0}`), 0o644)
	if f.ReadWait("worker") != nil {
		t.Fatal("a wait with no since was read")
	}
	os.WriteFile(f.waitFile("worker"), []byte(`[1]`), 0o644)
	if f.ReadWait("worker") != nil {
		t.Fatal("a wait that is not an object was read")
	}
}

func TestWaitOfNeedsARunningOpenSessionStillAtItsDialog(t *testing.T) {
	f := newFixture(t)
	row := f.agent("worker", Running, "h1")
	id := f.event("worker", "PermissionRequest")
	f.NoteWait("worker", "Bash", map[string]any{"command": "make"}, &id)

	f.term.blockers["h1"] = &term.Blocker{Kind: term.Modal}
	if f.WaitOf(row) == nil {
		t.Fatal("a wait at an open dialog was hidden")
	}
	if len(f.term.cachedNB) == 0 || f.term.cachedNB[0].Unix() != f.now {
		t.Errorf("the screen read was not asked to be newer than the wait: %v", f.term.cachedNB)
	}

	f.term.blockers["h1"] = nil
	if f.WaitOf(row) != nil {
		t.Fatal("a wait on a clean screen showed")
	}
	if f.ReadWait("worker") == nil {
		t.Fatal("a clean screen deleted the wait file")
	}
	f.term.blockers["h1"] = &term.Blocker{Kind: term.Backgrounded}
	if f.WaitOf(row) != nil {
		t.Fatal("the session picker kept the wait up")
	}
	f.term.blockers["h1"] = &term.Blocker{Kind: term.Unreadable}
	if f.WaitOf(row) == nil {
		t.Fatal("an unreadable screen hid a wait")
	}

	noTerm := *row
	noTerm.Terminal = ""
	if f.WaitOf(&noTerm) == nil {
		t.Fatal("no terminal must fail closed and show the wait")
	}

	closed := *row
	closed.ClosedAt = 5
	idle := *row
	idle.State = Idle
	if f.WaitOf(&closed) != nil || f.WaitOf(&idle) != nil || f.WaitOf(nil) != nil {
		t.Fatal("a closed or no longer RUNNING session is waiting")
	}
	if f.WaitOf(&AgentRow{Agent: "nobody", State: Running}) != nil {
		t.Fatal("an agent with no wait file is waiting")
	}
}

func TestWaitOfDropsAWaitTheAgentMovedPast(t *testing.T) {
	f := newFixture(t)
	row := f.agent("worker", Running, "h1")
	f.term.blockers["h1"] = &term.Blocker{Kind: term.Modal}
	id := f.event("worker", "PermissionRequest")
	f.NoteWait("worker", "Bash", map[string]any{"command": "make"}, &id)

	f.event("worker", "SubagentStop") // a sibling finishing is not the turn going on
	f.event("other", "report")        // nor is another agent
	if f.WaitOf(row) == nil {
		t.Fatal("a sibling's event cleared the wait")
	}
	f.event("worker", "report")
	if f.WaitOf(row) != nil {
		t.Fatal("the agent's own report did not end the wait")
	}
	if f.ReadWait("worker") != nil {
		t.Fatal("the stale wait file stayed")
	}

	// A wait noted without an id is left showing.
	f.NoteWait("worker", "Bash", map[string]any{"command": "make"}, nil)
	f.event("worker", "commit")
	if f.WaitOf(row) == nil {
		t.Fatal("a wait with no event id was guessed at")
	}
}
