package daemon

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/stream"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/tick"
	"github.com/handline/handline/go/internal/watcher"
)

// storyPorts is the timebox's real ports where the story needs them (the doing test, the lead, a notice written for the digest).
type storyPorts struct {
	tick.TimeboxPorts
	d *Daemon
}

func (p storyPorts) TaskDoing(q db.Q, t *db.Task) bool {
	s, err := db.TaskState(q, t)
	return err == nil && s == "doing"
}
func (p storyPorts) SupervisorOf(a string) string {
	tm, err := team.LoadTeam(p.d.cfg.Home)
	if err != nil || tm == nil {
		return ""
	}
	return tm.SupervisorOf(a)
}
func (p storyPorts) SendBatch(q db.Q, agent, text string) error {
	_, err := p.d.env().Notice(q, agent, text, "timebox")
	return err
}

type storyHost struct{ tick.Host }

func (storyHost) Agents() []string { return []string{"alice", "bob"} }

// One story through the real paths: a task the founder gave alice, a child bob does for it, three memos with no @, the child done, the
// parent done, and bob's own task running past its timeout. It counts what each recipient is told.
func TestAStoryTellsTheFounderOneNoticeAndAgentsOnlyWhatTheyMustActOn(t *testing.T) {
	d, _ := tellDaemon(t, true)
	if err := os.WriteFile(filepath.Join(d.cfg.Home, "team.yaml"), []byte(strings.Replace(tellTeam, "eng:", "alice:", 1)), 0o644); err != nil { // alice leads bob
		t.Fatal(err)
	}
	var entries []stream.Entry
	notes := &watcher.Notifications{Reads: &reads.Env{DB: d.db, Home: d.cfg.Home}}
	emit := func(e stream.Entry) (*stream.Event, error) { entries = append(entries, e); return &stream.Event{}, nil }
	if err := notes.Scan(d.db, emit); err != nil { // primes the cursors: what came before is history
		t.Fatal(err)
	}
	parent, err := db.NewTask(d.db, "the founder's job", "alice", db.Human, 0, db.DefaultPriority, "", 0, "")
	if err != nil {
		t.Fatal(err)
	}
	child, err := db.NewTask(d.db, "its part", "bob", "alice", parent, db.DefaultPriority, "", 0, "")
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AddBlock(d.db, parent, child); err != nil { // the parent waits on its child
		t.Fatal(err)
	}
	f := pipeTo(t, d, "")
	for i, text := range []string{"first thought", "second thought", "third thought"} {
		result(t, f.ask(fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":"task.memo.add","params":{"task":%d,"text":%q,"request_id":"story-m%d"}}`, 10+i, parent, text, i)))
	}
	f = pipeTo(t, d, "k") // the founder marks both done: the callers of this fixture are told apart by pid or key
	result(t, f.ask(fmt.Sprintf(`{"jsonrpc":"2.0","id":20,"method":"task.done","params":{"task":%d,"evidence":"built it","request_id":"story-c"}}`, child)))
	if _, err := d.env().ReleaseEndedBlockers(); err != nil {
		t.Fatal(err)
	}
	result(t, f.ask(fmt.Sprintf(`{"jsonrpc":"2.0","id":21,"method":"task.done","params":{"task":%d,"evidence":"all done","request_id":"story-p"}}`, parent)))

	// bob's own task, opened by alice, runs past its timeout
	slow, err := db.NewTask(d.db, "slow", "bob", "alice", 0, db.DefaultPriority, "", 100, "")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.SetTaskQueued(d.db, slow, "human"); err != nil {
		t.Fatal(err)
	}
	if _, _, err := d.env().AdvanceCurrentTask(d.db, "bob"); err != nil {
		t.Fatal(err)
	}
	tk := &tick.Tick{Q: d.db, Log: func(l string) { t.Log(l) }, Host: storyHost{}}
	box := &tick.Timebox{Ports: storyPorts{d: d}}
	box.SweepTaskTimeouts(tk, 1000)
	box.SweepTaskTimeouts(tk, 1200)

	if err := notes.Scan(d.db, emit); err != nil {
		t.Fatal(err)
	}
	count := func(pattern string) map[string]int {
		out := map[string]int{}
		rows, err := d.db.Query("SELECT target FROM requests WHERE requester = 'task-board' AND body LIKE ?", pattern)
		if err != nil {
			t.Fatal(err)
		}
		defer rows.Close()
		for rows.Next() {
			var to string
			rows.Scan(&to)
			out[to]++
		}
		return out
	}
	memo, result_, timeout := count("%memo added by%"), count("%done by%"), count("%past its%")
	founder := 0
	for _, e := range entries {
		if e.Type == "task_done" {
			founder++
		}
	}
	total := founder + len(memo) + len(result_) + len(timeout)
	for _, m := range []map[string]int{memo, result_, timeout} {
		for _, n := range m {
			total += n - 1
		}
	}
	t.Logf("founder task_done %d; memo notices %v; child-result notices %v; timeout notices %v; total %d", founder, memo, result_, timeout, total)
	if founder != 1 {
		t.Fatalf("the founder is told once, of their task done: %d", founder)
	}
	if len(memo) != 0 || len(result_) != 0 {
		t.Fatalf("no agent is told of a memo with no @ or of a child that did not fail: %v %v", memo, result_)
	}
	if len(timeout) != 2 || timeout["bob"] != 1 || timeout["alice"] != 1 {
		t.Fatalf("the timeout goes to its holder and the holder's lead, once each: %v", timeout)
	}
	if total != 3 {
		t.Fatalf("the story sends %d notices, want 3", total)
	}
}
