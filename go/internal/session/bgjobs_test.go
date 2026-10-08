package session

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestFooterCountAddsEveryNumber(t *testing.T) {
	for in, want := range map[string]int{"2 shells, 1 monitor": 3, "": 0, "no jobs": 0, "10 shells": 10} {
		if got := FooterCount(in); got != want {
			t.Errorf("FooterCount(%q) = %d, want %d", in, got, want)
		}
	}
}

func bgCall(command string, response any) map[string]any {
	return map[string]any{"tool_name": "Bash", "tool_input": map[string]any{"command": command, "run_in_background": true},
		"tool_response": response, "tool_use_id": "tu1", "session_id": "s1"}
}

func TestOnlyABackgroundBashCallIsNoted(t *testing.T) {
	f := newFixture(t)
	for _, raw := range []map[string]any{
		{"tool_name": "Edit", "tool_input": map[string]any{"run_in_background": true}},
		{"tool_name": "Bash", "tool_input": map[string]any{"command": "ls"}},
		{"tool_name": "Bash", "tool_input": map[string]any{"command": "ls", "run_in_background": "yes"}},
		{"tool_name": "Bash"},
	} {
		if err := f.NoteBackground("worker", raw); err != nil {
			t.Fatal(err)
		}
	}
	var n int
	f.DB.QueryRow("SELECT COUNT(*) FROM events").Scan(&n)
	if n != 0 {
		t.Fatalf("%d events for calls that started nothing", n)
	}
}

func TestRunningListsTheJobsWithNoExitLine(t *testing.T) {
	f := newFixture(t)
	dir := t.TempDir()
	out := func(id, body string) string {
		p := filepath.Join(dir, "sess", "tasks", id+".output")
		os.MkdirAll(filepath.Dir(p), 0o755)
		os.WriteFile(p, []byte(body), 0o644)
		return p
	}
	done := out("aaa", strings.Repeat("x", 500)+"\n[exited with code 0]\n")
	going := out("bbb", "still\n")
	f.NoteBackground("worker", bgCall("sleep 1", fmt.Sprintf("started, output in %s", done)))
	f.now++
	f.NoteBackground("worker", bgCall("make   watch", fmt.Sprintf(`{"backgroundTaskId": "bbb", "out": "%s"}`, going)))
	f.now++
	f.NoteBackground("worker", bgCall("tail -f log", map[string]any{"shellId": "ccc"}))
	f.now++
	f.NoteBackground("worker", bgCall("nothing said", "ok"))

	jobs, err := f.RunningJobs("worker", 0)
	if err != nil {
		t.Fatal(err)
	}
	var cmds, ids []string
	for _, j := range jobs {
		cmds, ids = append(cmds, j.Command), append(ids, j.Job)
	}
	if got := strings.Join(cmds, "|"); got != "make   watch|tail -f log|nothing said" {
		t.Errorf("running: %q", got)
	}
	if got := strings.Join(ids, "|"); got != "bbb|ccc|" {
		t.Errorf("job ids: %q", got)
	}
	if last, _ := f.RunningJobs("worker", 2); len(last) != 2 || last[0].Command != "tail -f log" {
		t.Errorf("last two: %v", last)
	}

	// The footer showing none ends everything recorded before it.
	f.now++
	f.event("worker", "background_work_ended")
	if after, _ := f.RunningJobs("worker", 0); len(after) != 0 {
		t.Errorf("jobs survived the footer showing none: %v", after)
	}
}

func TestLineCutsAndCountsTheTime(t *testing.T) {
	ago := func(s int64) string { return fmt.Sprintf("%ds", s) }
	j := BgJob{Command: "a \n  b", Started: 90}
	if got := j.Line(100, ago); got != "`a b` for 10s" {
		t.Errorf("got %q", got)
	}
	long := BgJob{Command: strings.Repeat("é", 150), Started: 100}
	want := "`" + strings.Repeat("é", 99) + "…` for 0s"
	if got := long.Line(50, ago); got != want {
		t.Errorf("got %q", got)
	}
}
