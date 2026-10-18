package heal

import (
	"fmt"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/store"
)

func openDB(t *testing.T) db.Q {
	d, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	must(t, err)
	t.Cleanup(func() { d.Close() })
	return d
}

// fakePorts records each step; onRestart, when set, is what a restart does to the agent's rows.
type fakePorts struct {
	person    bool
	did       []string
	cards     []string
	fixes     []string
	onRestart func()
}

func (f *fakePorts) Agents() []string        { return []string{"a"} }
func (f *fakePorts) NeedsPerson(string) bool { return f.person }
func (f *fakePorts) Interrupt(agent string) (bool, string) {
	f.did = append(f.did, "interrupt")
	return true, ""
}
func (f *fakePorts) Push(_ db.Q, agent string, rid int64) string {
	f.did = append(f.did, "push")
	return "delivered to " + agent
}
func (f *fakePorts) Restart(agent string, fresh bool) (bool, string, error) {
	if fresh {
		f.did = append(f.did, "relaunch")
	} else {
		f.did = append(f.did, "restart")
	}
	if f.onRestart != nil {
		f.onRestart()
	}
	return true, agent + ": restarted", nil
}
func (f *fakePorts) RaiseCard(_ db.Q, agent, text, fix string) error {
	f.cards = append(f.cards, text)
	f.fixes = append(f.fixes, fix)
	return nil
}

// hung leaves agent a RUNNING in its terminal with nothing heard for two hours.
func hung(t *testing.T, q db.Q, now int64) {
	t.Helper()
	must(t, db.SetAgentState(q, "a", states.Running, 0, ""))
	must(t, db.SetTerminal(q, "a", "%1", "wt"))
	if _, err := db.AddEventAt(q, now-7200, "a", "UserPromptSubmit"); err != nil {
		t.Fatal(err)
	}
}

func must(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}

// sweepFor sweeps every tick for d seconds from now.
func sweepFor(t *testing.T, h *Healer, q db.Q, now, d int64) {
	t.Helper()
	for at := now; at <= now+d; at += 30 {
		must(t, h.Sweep(q, at))
	}
}

func TestHungAgentRecoversByRestart(t *testing.T) {
	q := openDB(t)
	now := time.Now().Unix()
	hung(t, q, now)
	f := &fakePorts{}
	f.onRestart = func() {
		must(t, db.SetAgentState(q, "a", states.Idle, 0, "restarted"))
	}
	h := &Healer{Ports: f}

	must(t, h.Sweep(q, now))
	must(t, h.Sweep(q, now+30))
	if strings.Join(f.did, ",") != "interrupt" {
		t.Fatalf("first the retry, and nothing more before StepWaitS: %v", f.did)
	}
	sweepFor(t, h, q, now+StepWaitS, 10*StepWaitS)
	if strings.Join(f.did, ",") != "interrupt,restart" {
		t.Fatalf("an interrupt that did not take is followed by a restart, and the restart ends it: %v", f.did)
	}
	if len(f.cards) != 0 {
		t.Fatalf("a healed agent raises no card: %v", f.cards)
	}
	if step, _ := db.Cursor(q, stepKey("a")); step != 0 {
		t.Fatalf("the ladder is forgotten once healed, at step %d", step)
	}
	if id, ok, _ := db.LastActivityID(q, "a", []string{"healed"}); !ok || id == 0 {
		t.Fatal("no healed event")
	}
}

func TestAgentThatStaysStuckRaisesOneCard(t *testing.T) {
	q := openDB(t)
	now := time.Now().Unix()
	hung(t, q, now)
	tid, err := db.NewTask(q, "the work", "a", "b", 0, db.DefaultPriority, "", 0, "")
	must(t, err)
	must(t, db.SetCurrentTask(q, "a", tid, true))
	f := &fakePorts{}
	h := &Healer{Ports: f}

	sweepFor(t, h, q, now, 20*StepWaitS)
	if strings.Join(f.did, ",") != "interrupt,restart,relaunch" {
		t.Fatalf("each step once, in order: %v", f.did)
	}
	if len(f.cards) != 1 {
		t.Fatalf("exactly one card while it stays stuck, got %d", len(f.cards))
	}
	if !strings.Contains(f.cards[0], fmt.Sprintf("a is stuck on T%d: its turn is hung", tid)) || f.fixes[0] != FixRestartTerminal {
		t.Fatalf("the card names the agent, task and trouble, with the fix: %q %q", f.cards[0], f.fixes[0])
	}
}

func TestUndeliveredMessageIsPushed(t *testing.T) {
	q := openDB(t)
	now := time.Now().Unix()
	must(t, db.SetAgentState(q, "a", states.Idle, 0, ""))
	task, err := db.AddRequest(q, db.NewRequest{Requester: "b", Target: "a", Body: "a task", Kind: "task", Response: "async"})
	must(t, err)
	_, err = q.Exec("UPDATE requests SET ts = ? WHERE id = ?", now-3600, task)
	must(t, err)
	f := &fakePorts{}
	h := &Healer{Ports: f}
	must(t, h.Sweep(q, now))
	if len(f.did) != 0 {
		t.Fatalf("a task queued for an idle agent is the task queue's, not a stuck delivery: %v", f.did)
	}

	msg, err := db.AddRequest(q, db.NewRequest{Requester: "b", Target: "a", Body: "hello", Kind: "say", Response: "fire_and_forget"})
	must(t, err)
	must(t, h.Sweep(q, now))
	if len(f.did) != 0 {
		t.Fatalf("a fresh message is not stuck: %v", f.did)
	}
	_, err = q.Exec("UPDATE requests SET ts = ? WHERE id = ?", now-UndeliveredS, msg)
	must(t, err)
	must(t, h.Sweep(q, now))
	if strings.Join(f.did, ",") != "push" {
		t.Fatalf("a message waiting UndeliveredS is delivered again: %v", f.did)
	}
}

func TestPromptOnScreenIsLeftToThePerson(t *testing.T) {
	q := openDB(t)
	now := time.Now().Unix()
	hung(t, q, now)
	f := &fakePorts{person: true}
	sweepFor(t, &Healer{Ports: f}, q, now, 20*StepWaitS)
	if len(f.did) != 0 || len(f.cards) != 0 {
		t.Fatalf("an agent at a prompt has its own card; nothing is done to it: %v %v", f.did, f.cards)
	}
}
