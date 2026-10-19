package atomicfile

import (
	"os"
	"path/filepath"
	"testing"
)

func TestWriteLeavesNoTempFileAndReplaces(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "x.json")
	for _, text := range []string{"one", "two"} {
		if err := Write(p, text); err != nil {
			t.Fatal(err)
		}
	}
	if b, _ := os.ReadFile(p); string(b) != "two" {
		t.Fatalf("%q", b)
	}
	entries, _ := os.ReadDir(dir)
	if len(entries) != 1 {
		t.Fatalf("%v", entries)
	}
	if err := Write(filepath.Join(dir, "nowhere", "x"), "t"); err == nil {
		t.Fatal("wrote into a missing directory")
	}
}
