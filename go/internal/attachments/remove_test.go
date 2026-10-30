package attachments

import (
	"os"
	"testing"
)

func TestRemoveDeletesTheFileAtOnceAndLeavesATombstone(t *testing.T) {
	s, now, q := testStore(t)
	img := putAll(t, s, q, "a.png", "image/png", pngOf(2, 2), 1000)
	expired := putAll(t, s, q, "e.png", "image/png", pngOf(2, 2), 1000)
	s.remove(*expired)

	if _, already, err := s.Remove(q, img.ID, "founder", true); err != nil || already {
		t.Fatalf("a dry run: %v %v", already, err)
	}
	if _, err := os.Stat(s.Path(*img)); err != nil || s.Removed(img.ID) {
		t.Fatalf("a dry run keeps the file and leaves no tombstone: %v", err)
	}

	if _, already, err := s.Remove(q, img.ID, "founder", false); err != nil || already {
		t.Fatalf("the remove: %v %v", already, err)
	}
	if _, err := os.Stat(s.Path(*img)); !os.IsNotExist(err) {
		t.Fatalf("the file is deleted at once: %v", err)
	}
	var tomb tombstone
	if err := readJSON(s.sub("removed", img.ID+".json"), &tomb); err != nil || tomb.By != "founder" || tomb.RemovedAt != now.Unix() {
		t.Fatalf("the tombstone says when and by whom: %+v %v", tomb, err)
	}

	text := "![a](attachment:" + img.ID + ")![e](attachment:" + expired.ID + ")"
	parts := s.Parts(text, false)
	if p := parts[0].(map[string]any); p["gone"] != true || p["removed"] != true {
		t.Fatalf("a removed image reads gone and removed: %v", p)
	}
	if p := parts[1].(map[string]any); p["gone"] != true || p["removed"] != nil {
		t.Fatalf("an expired image reads gone only: %v", p)
	}
	if f := s.FileMap(*img, true); f["gone"] != true || f["removed"] != true || f["path"] != nil {
		t.Fatalf("a removed memo file reads gone and removed, with no path: %v", f)
	}

	if n, already, err := s.Remove(q, img.ID, "founder", false); err != nil || !already || n != 0 {
		t.Fatalf("a second remove is already, 0: %d %v %v", n, already, err)
	}
	if _, _, err := s.Remove(q, expired.ID, "founder", false); err == nil {
		t.Fatal("an id no longer kept and never removed is not_found")
	}
}
