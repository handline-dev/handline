package taskwrites

import (
	"fmt"
	"testing"
)

// A task an older daemon left in review is moved once at start: one not accepted goes to its owner's lead with its answer as a memo; an
// accepted one stays with its owner, queued, the memo naming its PR. A second start finds nothing to move.
func TestEndReviewsMovesEachTaskLeftInReview(t *testing.T) {
	home := t.TempDir()
	sdb, f, _ := reportEnvIn(t, home,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, reported_at, report, report_via) "+
			"VALUES(1, 'unaccepted', 'w', 'boss', 1, 'boss', 5, 'built it', 'reason')",
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, reported_at, report, report_via, pr, accepted_by, accepted_at) "+
			"VALUES(2, 'accepted', 'w', 'boss', 1, 'boss', 5, 'merges soon', 'reason', 12, 'lead', 6)",
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, reported_at, report, done_evidence) VALUES(3, 'ended', 'w', 'boss', 1, 5, 'x', 'x')",
	)
	said, err := EndReviews(sdb, home, f)
	if err != nil {
		t.Fatal(err)
	}
	if fmt.Sprint(said) != "[T1 left review: handed to lead T2 left review: queued with w]" {
		t.Fatalf("%q", said)
	}
	if count(t, sdb, "SELECT COUNT(*) FROM tasks WHERE id IN (1, 2) AND reported_at IS NULL AND report IS NULL AND accepted_by IS NULL") != 2 {
		t.Fatal("the answers are cleared")
	}
	if count(t, sdb, "SELECT COUNT(*) FROM tasks WHERE id = 1 AND owner = 'lead'") != 1 || count(t, sdb, "SELECT COUNT(*) FROM tasks WHERE id = 2 AND owner = 'w'") != 1 {
		t.Fatal("T1 goes to the lead, T2 stays")
	}
	if count(t, sdb, "SELECT COUNT(*) FROM task_memos WHERE task_id = 1 AND author = 'daemon' AND body LIKE '%built it%'") != 1 ||
		count(t, sdb, "SELECT COUNT(*) FROM task_memos WHERE task_id = 2 AND body LIKE '%accepted by lead; it waits on PR #12%'") != 1 {
		t.Fatal("each answer is kept as a memo")
	}
	if len(f.handed) != 1 || f.handed[0].To != "lead" || f.handed[0].By != "daemon" {
		t.Fatalf("%+v", f.handed)
	}
	if again, err := EndReviews(sdb, home, f); err != nil || len(again) != 0 {
		t.Fatalf("%q %v", again, err)
	}
}

// With no lead, a review left open goes to the task's reporter, else its opener; one that would go back to its own owner stays, queued.
// A handoff that is refused leaves that task for the next start, and the rest still move.
func TestEndReviewsFallsBackToTheReporterThenTheOpener(t *testing.T) {
	home := t.TempDir()
	sdb, f, _ := reportEnvIn(t, home,
		"INSERT INTO tasks(id, title, owner, opened_by, reporter, created_at, queued_by, reported_at, report) "+
			"VALUES(1, 'to the founder', 'boss', 'w', 'human', 1, 'w', 5, 'a'), (2, 'its own', 'boss', 'boss', NULL, 1, 'boss', 5, 'b')",
	)
	said, err := EndReviews(sdb, home, f)
	if err != nil || fmt.Sprint(said) != "[T1 left review: queued with boss T2 left review: queued with boss]" {
		t.Fatalf("%q %v", said, err)
	}
	if len(f.handed) != 0 || count(t, sdb, "SELECT COUNT(*) FROM requests WHERE requester = 'daemon' AND target = 'human' AND state = 'queued'") != 1 {
		t.Fatalf("a task whose reporter is the founder stays and asks them: %+v", f.handed)
	}
	sdb, f, _ = reportEnvIn(t, t.TempDir(),
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, reported_at, report) VALUES(1, 'refused', 'w', 'boss', 1, 'boss', 5, 'a')",
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, reported_at, report, accepted_by) VALUES(2, 'stays', 'w', 'boss', 1, 'boss', 5, 'b', 'lead')",
	)
	f.deny = "no route"
	said, err = EndReviews(sdb, home, f)
	if err == nil || fmt.Sprint(said) != "[T2 left review: queued with w]" || count(t, sdb, "SELECT COUNT(*) FROM tasks WHERE id = 1 AND reported_at IS NOT NULL") != 1 {
		t.Fatalf("%q %v", said, err)
	}
}
