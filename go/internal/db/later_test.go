package db

import (
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/store"
)

// A reader that ends a deferral ends the row it read: one replaced since is not its to delete.
func TestLaterDropDeletesOnlyTheRowThatWasRead(t *testing.T) {
	d, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	t1, t2, after := int64(2_000), int64(3_000), int64(5)
	if err := LaterSet(d, LaterTask, "1", &t1, nil, 100); err != nil {
		t.Fatal(err)
	}
	read, err := LaterRows(d)
	if err != nil || len(read) != 1 {
		t.Fatalf("%v %v", read, err)
	}
	// the founder defers it again between the read and the delete
	if err := LaterSet(d, LaterTask, "1", &t2, nil, 100); err != nil {
		t.Fatal(err)
	}
	if ok, err := LaterDrop(d, read[0]); err != nil || ok {
		t.Fatalf("dropped a row it did not read: %v %v", ok, err)
	}
	if err := LaterSet(d, LaterTask, "1", nil, &after, 100); err != nil {
		t.Fatal(err)
	}
	now, _ := LaterRows(d)
	if ok, err := LaterDrop(d, now[0]); err != nil || !ok {
		t.Fatalf("the row as read was not dropped: %v %v", ok, err)
	}
	if rows, _ := LaterRows(d); len(rows) != 0 {
		t.Fatalf("left %v", rows)
	}
}
