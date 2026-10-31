package taskwrites

import (
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/store"
)

// The daemon closes a question of the founder's that has gone moot: the task is done with its words, its card leaves the queue, and
// what follows a done is told. A second close is refused.
func TestTheDaemonClosesTheFoundersTaskWithItsWords(t *testing.T) {
	d, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	f := &fakeTeller{}
	tid, err := AskFounder(d, f, "18% will be lost at 02:10", "body", "spare-notice")
	if err != nil {
		t.Fatal(err)
	}
	if n := count(t, d, "SELECT COUNT(*) FROM requests r JOIN task_requests l ON l.request_id = r.id WHERE l.task_id = 1 AND r.state = 'queued'"); n != 1 {
		t.Fatalf("the question is a card waiting on the founder: %d", n)
	}
	if err := CloseFounderTask(d, t.TempDir(), f, tid, "It was lost."); err != nil {
		t.Fatal(err)
	}
	if n := count(t, d, "SELECT COUNT(*) FROM tasks WHERE id = 1 AND done_evidence = 'It was lost.'"); n != 1 {
		t.Fatal("the task is not done with its words")
	}
	if n := count(t, d, "SELECT COUNT(*) FROM requests r JOIN task_requests l ON l.request_id = r.id WHERE l.task_id = 1 AND r.state = 'queued'"); n != 0 {
		t.Fatalf("the card still waits on the founder: %d", n)
	}
	if len(f.done) != 1 {
		t.Fatalf("done told %v", f.done)
	}
	if err := CloseFounderTask(d, t.TempDir(), f, tid, "again"); err == nil {
		t.Fatal("a closed task was closed again")
	}
}
