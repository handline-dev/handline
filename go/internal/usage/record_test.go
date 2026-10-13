package usage

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func pct(n int) *int { return &n }

func readBack(t *testing.T, p string) map[string]any {
	t.Helper()
	b, err := os.ReadFile(p)
	if err != nil {
		t.Fatal(err)
	}
	var v any
	if err := decodeNumbers(b, &v); err != nil {
		t.Fatal(err)
	}
	return v.(map[string]any)
}

func TestRecordKeepsWhatIsThereAndTakesAnEarlyResetInsideItsWindow(t *testing.T) {
	p := filepath.Join(t.TempDir(), "status.json")
	prev := `{"session_id":"s1","transcript_path":"/t","windows":[{"minutes":300,"used_percent":40,"resets_at":2000,"observed_at":1000},
	  {"minutes":10080,"used_percent":10,"resets_at":900,"observed_at":1000}]}`
	if err := os.WriteFile(p, []byte(prev), 0o644); err != nil {
		t.Fatal(err)
	}
	r := Reading{Account: "a", At: 1500, Windows: []ReadingWindow{{Minutes: 300, Pct: pct(35), Reset: 2000}, {Minutes: 10080, Pct: pct(3), Reset: 5000}}}
	if !RecordFile(p, r) {
		t.Fatal("not written")
	}
	got := readBack(t, p)
	if got["session_id"] != "s1" || got["transcript_path"] != "/t" || got["rl_source"] != "api" {
		t.Errorf("merge lost fields: %v", got)
	}
	ws := entries(got)
	if n, _ := isInt(ws[0]["used_percent"]); n != 35 {
		t.Errorf("an early reset with the same resets_at must be taken, got %v", ws[0]["used_percent"])
	}
	if n, _ := isInt(ws[1]["used_percent"]); n != 3 {
		t.Errorf("7d across a rollover must be replaced, got %v", ws[1]["used_percent"])
	}
	if n, _ := isInt(got["collected_at"]); n != 1500 {
		t.Errorf("collected_at = %v", got["collected_at"])
	}
}

func TestRecordDoesNotLetALateReadingUndoANewerOne(t *testing.T) {
	p := filepath.Join(t.TempDir(), "status.json")
	if err := os.WriteFile(p, []byte(`{"windows":[{"minutes":10080,"used_percent":60,"resets_at":9000,"observed_at":2000}]}`), 0o644); err != nil {
		t.Fatal(err)
	}
	RecordFile(p, Reading{Account: "a", At: 1500, Windows: []ReadingWindow{{Minutes: 10080, Pct: pct(50), Reset: 9000}}})
	if n, _ := isInt(entries(readBack(t, p))[0]["used_percent"]); n != 60 {
		t.Errorf("a reading older than the held one lowered it to %v", n)
	}
}

func TestRecordKeepsUnknownAndUntouchedWindowsAsNull(t *testing.T) {
	p := filepath.Join(t.TempDir(), "status.json")
	if !RecordFile(p, Reading{Account: "a", At: 10, Windows: []ReadingWindow{{Minutes: 300}}}) {
		t.Fatal("not written")
	}
	e := entries(readBack(t, p))[0]
	if e["used_percent"] != nil || e["resets_at"] != nil {
		t.Errorf("unreported window must stay null: %v", e)
	}
}

func TestRecordDoesNotMakeAnAccountDirectory(t *testing.T) {
	if RecordFile(filepath.Join(t.TempDir(), "gone", "status.json"), Reading{Account: "a", At: 1}) {
		t.Error("wrote into a missing directory")
	}
	_ = json.Number("")
}
