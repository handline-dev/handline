package handoff

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// testdata/transcripts holds transcripts and what the Python cachettl.read answered for each.
func TestTheCacheClockReadsLikePython(t *testing.T) {
	b, err := os.ReadFile("testdata/transcripts/expected.json")
	if err != nil {
		t.Fatal(err)
	}
	var want map[string]*struct {
		At  float64
		TTL *int
	}
	if err := json.Unmarshal(b, &want); err != nil {
		t.Fatal(err)
	}
	for name, w := range want {
		got := ReadClock(filepath.Join("testdata", "transcripts", name+".jsonl"))
		switch {
		case w == nil && got != nil:
			t.Errorf("%s: a clock where Python has none: %+v", name, got)
		case w != nil && got == nil:
			t.Errorf("%s: no clock, Python has %+v", name, w)
		case w != nil:
			ttl := 0
			if w.TTL != nil {
				ttl = *w.TTL
			}
			if got.At != w.At || got.TTL != ttl {
				t.Errorf("%s: %+v, Python %+v", name, got, w)
			}
		}
	}
}

func TestAClockCountsDownAndIsUnknownWithoutALife(t *testing.T) {
	c := Clock{At: 1000, TTL: LongTTLS}
	if left, ok := c.Left(1000 + 3000); !ok || left != 600 {
		t.Errorf("left %v %v", left, ok)
	}
	if left, _ := c.Left(1000 + 4000); left != -400 {
		t.Errorf("past the life: %v", left)
	}
	if _, ok := (Clock{At: 1}).Left(2); ok {
		t.Error("a clock without a life has seconds left")
	}
	if Margin(LongTTLS) != 300 || Margin(ShortTTLS) != 25 {
		t.Errorf("margins %d %d", Margin(LongTTLS), Margin(ShortTTLS))
	}
}

func TestNothingToReadIsNoClock(t *testing.T) {
	if ReadClock("") != nil || ReadClock(filepath.Join(t.TempDir(), "missing.jsonl")) != nil {
		t.Error("a clock for a transcript that is not there")
	}
}

func TestAGrowingFileIsReadAgainAndAnUnchangedOneFromMemory(t *testing.T) {
	p := filepath.Join(t.TempDir(), "t.jsonl")
	line := func(ts string) string {
		return `{"type":"assistant","timestamp":"` + ts + `","message":{"usage":{"cache_creation":{"ephemeral_1h_input_tokens":9}}}}` + "\n"
	}
	os.WriteFile(p, []byte(line("2026-10-08T01:00:00Z")), 0o644)
	first := ReadClock(p)
	if first == nil || first != ReadClock(p) {
		t.Fatalf("not answered from memory: %+v", first)
	}
	os.WriteFile(p, []byte(line("2026-10-08T01:00:00Z")+line("2026-10-08T01:30:00Z")), 0o644)
	if later := ReadClock(p); later == nil || later.At != first.At+1800 {
		t.Errorf("a grown file was not read again: %+v", later)
	}
}
