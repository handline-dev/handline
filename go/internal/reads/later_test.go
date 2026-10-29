package reads

import (
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
)

func laterOfTask(t *testing.T, e *Env, id int64) map[string]any {
	t.Helper()
	got, err := e.Tasks([]int64{id})
	if err != nil {
		t.Fatal(err)
	}
	later, _ := got[id]["later"].(map[string]any)
	return later
}

// A deferred task carries when it returns, and loses it the moment the time passes or the task it waits for ends, before the watcher
// has deleted the row.
func TestADeferredTaskCarriesLaterUntilItReturns(t *testing.T) {
	e := testEnv(t)
	for i := 1; i <= 2; i++ {
		if _, err := e.DB.Exec("INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(?, 't', 'human', 'boss', 1)", i); err != nil {
			t.Fatal(err)
		}
	}
	now := time.Now().Unix()
	if got := laterOfTask(t, e, 1); got != nil {
		t.Fatalf("not deferred: %v", got)
	}
	until := now + 3600
	if err := db.LaterSet(e.DB, db.LaterTask, "1", &until, nil, now); err != nil {
		t.Fatal(err)
	}
	if got := laterOfTask(t, e, 1); got["until"] != ts(until) || len(got) != 1 {
		t.Fatalf("until: %v", got)
	}
	past := now - 1
	if err := db.LaterSet(e.DB, db.LaterTask, "1", &past, nil, now); err != nil {
		t.Fatal(err)
	}
	if got := laterOfTask(t, e, 1); got != nil {
		t.Fatalf("its time passed: %v", got)
	}
	after := int64(2)
	if err := db.LaterSet(e.DB, db.LaterTask, "1", nil, &after, now); err != nil {
		t.Fatal(err)
	}
	if got := laterOfTask(t, e, 1); got["after_task"] != int64(2) || len(got) != 1 {
		t.Fatalf("after_task: %v", got)
	}
	if _, err := e.DB.Exec("UPDATE tasks SET done_evidence = 'x', done_at = 5 WHERE id = 2"); err != nil {
		t.Fatal(err)
	}
	if got := laterOfTask(t, e, 1); got != nil {
		t.Fatalf("the task it waited for is done: %v", got)
	}
}

// A comment's ask to the founder carries when it returns, on the card the founder is shown.
func TestADeferredCommentAskCarriesLater(t *testing.T) {
	e := testEnv(t)
	for _, q := range []string{
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 't', 'w', 'boss', 1), (2, 'u', 'w', 'boss', 1)",
		"INSERT INTO task_memos(id, task_id, author, body, created_at) VALUES(7, 1, 'w', '@human which?', 1)",
		"INSERT INTO requests(id, ref, ts, requester, target, body, state, memo_id) VALUES(70, 'r', 1, 'w', 'human', '@human which?', 'queued', 7)",
	} {
		if _, err := e.DB.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
	laterOnCard := func() map[string]any {
		items, err := e.FounderItems(0)
		if err != nil || len(items) != 1 || items[0]["memo"] != int64(7) {
			t.Fatalf("%v %v", items, err)
		}
		got, _ := items[0]["later"].(map[string]any)
		return got
	}
	if got := laterOnCard(); got != nil {
		t.Fatalf("not deferred: %v", got)
	}
	after, now := int64(2), time.Now().Unix()
	if err := db.LaterSet(e.DB, db.LaterComment, "7", nil, &after, now); err != nil {
		t.Fatal(err)
	}
	if got := laterOnCard(); got["after_task"] != int64(2) {
		t.Fatalf("after_task: %v", got)
	}
}
