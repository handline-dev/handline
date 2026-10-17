package db

import (
	"strings"
	"testing"
)

func TestMemosAreAddedReadEditedAndDeletedWithAFractionalStamp(t *testing.T) {
	q := open(t)
	tid := seedTask(t, q, "b", "a")
	mid, err := AddMemo(q, tid, "a", "first")
	if err != nil || mid == 0 {
		t.Fatal(mid, err)
	}
	m, err := GetMemo(q, mid)
	if err != nil || m == nil || m.TaskID != tid || m.Author != "a" || m.Body != "first" || m.EditedAt.Valid {
		t.Fatalf("%+v %v", m, err)
	}
	if m.CreatedAt <= 1e9 {
		t.Fatalf("%v", m.CreatedAt)
	}
	if err := EditMemo(q, mid, "w", "second"); err != nil {
		t.Fatal(err)
	}
	if m, _ := GetMemo(q, mid); m.Body != "second" || !m.EditedAt.Valid || m.EditedAt.Float64 != 1000 {
		t.Fatalf("%+v", m)
	}
	if s, _ := MemoSummary(q, tid, 500); s == "" {
		t.Fatal("a fractional stamp breaks the summary")
	}
	if err := DeleteMemo(q, mid, "w"); err != nil {
		t.Fatal(err)
	}
	if m, err := GetMemo(q, mid); m != nil || err != nil {
		t.Fatalf("%+v %v", m, err)
	}
}

// A memo's add, edit and delete are each an event with who and the text before and after: the task's history keeps a deleted memo's words.
func TestAMemoChangeIsKeptAsAnEventWithItsTextBeforeAndAfter(t *testing.T) {
	q := open(t)
	tid := seedTask(t, q, "b", "a")
	mid, _ := AddMemo(q, tid, "a", "first")
	EditMemo(q, mid, "b", "second")
	DeleteMemo(q, mid, "human")
	rows, err := q.Query("SELECT agent, kind, payload FROM events WHERE kind LIKE 'memo_%' ORDER BY id")
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var got []string
	for rows.Next() {
		var agent, kind, payload string
		rows.Scan(&agent, &kind, &payload)
		got = append(got, agent+" "+kind+" "+payload)
	}
	want := []string{
		`a memo_add {"task_id": 1, "memo": 1, "now": "first"}`,
		`b memo_edit {"task_id": 1, "memo": 1, "was": "first", "now": "second"}`,
		`human memo_delete {"task_id": 1, "memo": 1, "was": "second"}`,
	}
	if strings.Join(got, "\n") != strings.Join(want, "\n") {
		t.Fatalf("%q", got)
	}
	if err := EditMemo(q, 99, "a", "x"); err != nil {
		t.Fatal("a missing memo is not an error here: its caller says no memo")
	}
}
