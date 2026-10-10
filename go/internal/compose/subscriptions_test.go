package compose

import (
	"os"
	"path/filepath"
	"testing"

	dbpkg "github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
)

func TestTheFounderCloserMarksTheCardDoneAsTheFounder(t *testing.T) {
	_, _, env, home := wired(t)
	card, _ := dbpkg.AddRequest(env.DB, dbpkg.NewRequest{Requester: "alice", Target: "human", Body: "ends", Response: "async", Kind: "task"})
	tid, _ := dbpkg.NewTask(env.DB, "ends", "human", "alice", 0, dbpkg.DefaultPriority, "decision", 0)
	dbpkg.LinkRequest(env.DB, tid, card)
	c := founderCloser{env: env, home: home}
	ok, msg, err := c.MarkDoneByFounder(env.DB, tid, "closed by the daemon: ended")
	if err != nil || !ok {
		t.Fatalf("ok=%v msg=%q err=%v", ok, msg, err)
	}
	var ev string
	env.DB.QueryRow("SELECT done_evidence FROM tasks WHERE id = ?", tid).Scan(&ev)
	if ev != "closed by the daemon: ended" {
		t.Fatalf("evidence %q", ev)
	}
	if ok, msg, err := c.MarkDoneByFounder(env.DB, tid, "again"); err != nil || ok || msg == "" {
		t.Fatalf("a done task refuses with a reason: ok=%v msg=%q err=%v", ok, msg, err)
	}
}

func TestAPlanThatCannotBeReadLeavesTheRecordedOne(t *testing.T) {
	_, _, _, home := wired(t)
	f := filepath.Join(paths.AccountDir(home, "a1"), "subscription.json")
	os.MkdirAll(filepath.Dir(f), 0o755)
	if err := os.WriteFile(f, []byte(`{"subscriptionType":"pro"}`), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", t.TempDir()) // no claude to ask
	got, err := vendorPlans{home}.Refresh("a1")
	if err != nil || got != "pro" {
		t.Fatalf("got %q, %v", got, err)
	}
}
