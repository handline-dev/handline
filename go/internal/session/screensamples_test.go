package session

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestASampleIsKeptOnceAnIntervalAndTheNewestStay(t *testing.T) {
	f := newFixture(t)
	row := f.agent("worker", Running, "h1")
	tail := []string{"one", "two"}
	if f.RecordScreenSample("worker", row, "2 shells", nil) {
		t.Fatal("an empty screen was sampled")
	}
	if !f.RecordScreenSample("worker", row, "2 shells", tail) {
		t.Fatal("the first sample was not written")
	}
	path := filepath.Join(SamplesDir(f.Home), fmt.Sprintf("worker-%d.txt", f.now))
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"agent: worker\n", "db state: RUNNING\n", "session says: 2 shells\n", "rows: 2 (bottom of the screen, top first)\n", strings.Repeat("-", 60) + "\none\ntwo\n"} {
		if !strings.Contains(string(b), want) {
			t.Errorf("sample lacks %q:\n%s", want, b)
		}
	}
	if st, _ := os.Stat(path); st.Mode().Perm() != 0o600 {
		t.Errorf("mode %v: screens are private", st.Mode().Perm())
	}
	f.now += SampleIntervalS - 1
	if f.RecordScreenSample("worker", row, "x", tail) {
		t.Fatal("a second sample inside the interval")
	}
	for i := 0; i < SampleKeep+5; i++ {
		f.now += SampleIntervalS
		if !f.RecordScreenSample("worker", nil, "x", tail) {
			t.Fatalf("sample %d not written", i)
		}
		// mtimes tie inside a test: make each file older than the next.
		os.Chtimes(filepath.Join(SamplesDir(f.Home), fmt.Sprintf("worker-%d.txt", f.now)), timeAt(f.now), timeAt(f.now))
	}
	files, _ := filepath.Glob(filepath.Join(SamplesDir(f.Home), "*.txt"))
	if len(files) != SampleKeep {
		t.Errorf("%d samples kept, want %d", len(files), SampleKeep)
	}
	if _, err := os.Stat(path); err == nil {
		t.Error("the oldest sample stayed")
	}
}
