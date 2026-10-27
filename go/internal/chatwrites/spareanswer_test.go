package chatwrites

import (
	"fmt"
	"strings"
	"testing"

	dbpkg "github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/sparenotice"
)

// askedSpare is the founder's notice listing two spare drafts and one already queued task, as the sweep raises it.
func askedSpare(t *testing.T, r *rig) (notice int64, drafts []int64, queued int64) {
	t.Helper()
	mk := func(title string) int64 {
		tid, err := dbpkg.NewTask(r.db, title, "bob", "boss", 0, dbpkg.DefaultPriority, "", 0, "")
		if err != nil {
			t.Fatal(err)
		}
		return tid
	}
	drafts = []int64{mk("audit"), mk("tests")}
	queued = mk("already going")
	r.db.Exec("UPDATE tasks SET queued_by = 'boss', queued_at = 1 WHERE id = ?", queued)
	card := r.request("human", "human", fmt.Sprintf("18%% of theta's weekly use will be lost at 02:10.\nAccount: theta\nTasks: %s", sparenotice.TaskList(append(drafts, queued))))
	notice, err := dbpkg.NewTask(r.db, "18% of theta's weekly use will be lost", "human", "human", 0, dbpkg.DefaultPriority, "decision", 0, "")
	if err != nil {
		t.Fatal(err)
	}
	dbpkg.LinkRequest(r.db, notice, card)
	r.db.Exec("INSERT INTO task_tags(task_id, tag) VALUES(?, ?)", notice, sparenotice.NoticeTag)
	return notice, drafts, queued
}

func TestStartOnASpareNoticeQueuesTheDraftsItListsAndSkipsWhatIsNoLongerOne(t *testing.T) {
	r := newRig(t)
	notice, drafts, queued := askedSpare(t, r)
	tx, _ := r.db.Begin()
	defer tx.Rollback()
	handled, said, ids, err := answerSpare(tx, r.home, notice, "Start")
	if !handled || err != nil || len(ids) != 2 || ids[0] != drafts[0] || ids[1] != drafts[1] ||
		!strings.Contains(said, "queued "+sparenotice.TaskList(drafts)) || !strings.Contains(said, fmt.Sprintf("skipped T%d (queued)", queued)) {
		t.Fatalf("%v %q %v %v", handled, said, ids, err)
	}
	var n int
	tx.QueryRow("SELECT COUNT(*) FROM tasks WHERE queued_by = 'human' AND id IN (?, ?)", drafts[0], drafts[1]).Scan(&n)
	if n != 2 {
		t.Fatalf("both drafts are queued by the founder: %d", n)
	}
}

func TestAnyOtherAnswerToASpareNoticeJustClosesItAndOtherTasksAreLeftAlone(t *testing.T) {
	r := newRig(t)
	notice, drafts, _ := askedSpare(t, r)
	tx, _ := r.db.Begin()
	defer tx.Rollback()
	if handled, _, ids, err := answerSpare(tx, r.home, notice, "not now"); handled || err != nil || len(ids) != 0 {
		t.Fatalf("a dismissal acts on nothing: %v %v %v", handled, ids, err)
	}
	if handled, _, _, err := answerSpare(tx, r.home, drafts[0], "start"); handled || err != nil {
		t.Fatalf("a task without the notice tag is not one: %v %v", handled, err)
	}
	var n int
	tx.QueryRow("SELECT COUNT(*) FROM tasks WHERE queued_by = 'human'").Scan(&n)
	if n != 0 {
		t.Fatalf("nothing was queued: %d", n)
	}
}
