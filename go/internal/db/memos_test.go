package db

import "testing"

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
	if err := EditMemo(q, mid, "second"); err != nil {
		t.Fatal(err)
	}
	if m, _ := GetMemo(q, mid); m.Body != "second" || !m.EditedAt.Valid || m.EditedAt.Float64 != 1000 {
		t.Fatalf("%+v", m)
	}
	if s, _ := MemoSummary(q, tid, 500); s == "" {
		t.Fatal("a fractional stamp breaks the summary")
	}
	if err := DeleteMemo(q, mid); err != nil {
		t.Fatal(err)
	}
	if m, err := GetMemo(q, mid); m != nil || err != nil {
		t.Fatalf("%+v %v", m, err)
	}
}
