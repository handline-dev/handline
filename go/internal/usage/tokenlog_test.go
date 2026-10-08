package usage

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
)

const callLine = `{"type":"assistant","requestId":"r1","sessionId":"s1","cwd":"/w","timestamp":"2026-10-08T12:00:00.250Z","message":` +
	`{"model":"claude-opus-5-5","usage":{"input_tokens":3,"cache_creation_input_tokens":4,"cache_read_input_tokens":5,"output_tokens":6,` +
	`"output_tokens_details":{"thinking_tokens":2}}}}`

func TestParseCallReadsOnlyAssistantLinesWithAUsage(t *testing.T) {
	c, ok := ParseCall([]byte(callLine))
	want := Call{Session: "s1", Req: "r1", TS: 1791460800, Model: "claude-opus-5-5", Cwd: "/w", Tokens: Tokens{3, 4, 5, 6, 2}}
	if !ok || c != want {
		t.Fatalf("%+v %v", c, ok)
	}
	for name, line := range map[string]string{
		"no usage":     strings.Replace(callLine, `"usage"`, `"use"`, 1),
		"not assitant": strings.Replace(callLine, `"assistant"`, `"user"`, 1),
		"no request":   strings.Replace(callLine, `"requestId":"r1",`, ``, 1),
		"synthetic":    strings.Replace(callLine, `claude-opus-5-5`, `<synthetic>`, 1),
		"no model":     strings.Replace(callLine, `"model":"claude-opus-5-5",`, ``, 1),
		"bad stamp":    strings.Replace(callLine, `2026-10-08T12:00:00.250Z`, `yesterday`, 1),
		"no offset":    strings.Replace(callLine, `.250Z`, ``, 1),
		"bad count":    strings.Replace(callLine, `"input_tokens":3`, `"input_tokens":"3"`, 1),
		"not json":     `{"usage"`,
	} {
		if _, ok := ParseCall([]byte(line)); ok {
			t.Errorf("%s was taken for a call", name)
		}
	}
	side := strings.Replace(callLine, `"cwd":"/w",`, `"cwd":"/w","isSidechain":true,`, 1)
	if c, _ := ParseCall([]byte(side)); c.Side != 1 {
		t.Fatal("sidechain")
	}
}

func tokenEnv(t *testing.T) (*Env, db.Q, string) {
	e, q, _ := burnEnv(t)
	e.Now = func() float64 { return float64(time.Now().Unix()) } // the files carry the real modification time
	dir := filepath.Join(paths.AccountDir(e.Home, "a"), ".claude", "projects", "p")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	return e, q, dir
}

func TestIngestReadsAFileFromWhereItStoppedAndOnlyWholeLines(t *testing.T) {
	e, q, dir := tokenEnv(t)
	f := filepath.Join(dir, "s1.jsonl")
	more := strings.Replace(callLine, `"output_tokens":6`, `"output_tokens":60`, 1)
	os.WriteFile(f, []byte(callLine+"\n"+more+"\n"+`{"type":"assistant","requ`), 0o644)
	if n, err := e.IngestTokens(q, "a", BytesPerTick); err != nil || n != 2 {
		t.Fatalf("%d %v", n, err)
	}
	tot, _ := TokenTotals(q, "a", 0, 1<<40)
	if tot != (Tokens{3, 4, 5, 60, 2}) {
		t.Fatalf("one request streamed over two lines keeps its largest: %+v", tot)
	}
	if n, _ := e.IngestTokens(q, "a", BytesPerTick); n != 0 {
		t.Fatalf("nothing new: %d", n)
	}
	fh, _ := os.OpenFile(f, os.O_APPEND|os.O_WRONLY, 0)
	fh.WriteString(`est"}` + "\n" + strings.Replace(callLine, `"r1"`, `"r2"`, 1) + "\n")
	fh.Close()
	if n, _ := e.IngestTokens(q, "a", BytesPerTick); n != 1 {
		t.Fatalf("the half line was finished, and is no call: %d", n)
	}
	if tot, _ = TokenTotals(q, "a", 0, 1<<40); tot.Inp != 6 {
		t.Fatalf("%+v", tot)
	}
	if tot, _ = TokenTotals(q, "a", 1791460800, 1<<40); tot != (Tokens{}) {
		t.Fatalf("(lo, hi] excludes lo: %+v", tot)
	}
}

func TestIngestSpendsABudgetPerTickAndReadsAnOversizeRecordWhole(t *testing.T) {
	e, q, dir := tokenEnv(t)
	os.WriteFile(filepath.Join(dir, "s1.jsonl"), []byte(callLine+"\n"+strings.Replace(callLine, `"r1"`, `"r2"`, 1)+"\n"), 0o644)
	if n, _ := e.IngestTokens(q, "a", len(callLine)+10); n != 1 {
		t.Fatalf("a slice per tick: %d", n)
	}
	if n, _ := e.IngestTokens(q, "a", len(callLine)+10); n != 1 {
		t.Fatalf("the rest next tick: %d", n)
	}
	os.WriteFile(filepath.Join(dir, "s2.jsonl"), []byte(strings.Replace(callLine, `"r1"`, `"r3"`, 1)+"\n"), 0o644)
	if n, _ := e.IngestTokens(q, "a", 20); n != 1 {
		t.Fatalf("a record longer than the budget must not stall its file: %d", n)
	}
}

func TestIngestSkipsOldUnknownFilesLinksAndOtherVendors(t *testing.T) {
	e, q, dir := tokenEnv(t)
	old := filepath.Join(dir, "old.jsonl")
	os.WriteFile(old, []byte(callLine+"\n"), 0o644)
	ts := time.Now().Add(-(BackfillDays + 1) * 24 * time.Hour)
	os.Chtimes(old, ts, ts)
	secret := filepath.Join(e.Home, "secret.jsonl")
	os.WriteFile(secret, []byte(callLine+"\n"), 0o644)
	os.Symlink(secret, filepath.Join(dir, "link.jsonl"))
	os.WriteFile(filepath.Join(dir, "notes.txt"), []byte(callLine+"\n"), 0o644)
	if n, _ := e.IngestTokens(q, "a", BytesPerTick); n != 0 {
		t.Fatalf("only recent regular .jsonl files: %d", n)
	}
	if n, _ := e.IngestTokens(q, "nobody", BytesPerTick); n != 0 {
		t.Fatal("an account that is not there")
	}
	os.WriteFile(filepath.Join(paths.AccountDir(e.Home, "a"), "vendor"), []byte("opencode"), 0o644)
	os.WriteFile(filepath.Join(dir, "new.jsonl"), []byte(callLine+"\n"), 0o644)
	if n, _ := e.IngestTokens(q, "a", BytesPerTick); n != 0 {
		t.Fatal("another vendor has no transcripts here")
	}
}

func TestPruneTokensDropsWhatIsOlderThanSixtyDays(t *testing.T) {
	e, q, dir := tokenEnv(t)
	os.WriteFile(filepath.Join(dir, "s.jsonl"), []byte(callLine+"\n"), 0o644)
	e.IngestTokens(q, "a", BytesPerTick) // ts 2026-10-08, the clock is 2027-01-15
	if n, err := e.PruneTokens(q); err != nil || n != 0 {
		t.Fatalf("%d %v", n, err)
	}
	e.Now = func() float64 { return float64(1791460800 + (TokenRetentionDays+1)*86400) }
	if n, err := e.PruneTokens(q); err != nil || n != 1 {
		t.Fatalf("%d %v", n, err)
	}
}

func TestIngestPassesOverARecordLongerThanTheLimit(t *testing.T) {
	e, q, dir := tokenEnv(t)
	huge := strings.Repeat("x", MaxRecordBytes+1)
	os.WriteFile(filepath.Join(dir, "s.jsonl"), []byte(huge+"\n"+callLine+"\n"), 0o644)
	if n, _ := e.IngestTokens(q, "a", 1024); n != 0 {
		t.Fatalf("the record is passed over, not read: %d", n)
	}
	if n, _ := e.IngestTokens(q, "a", 1024); n != 1 {
		t.Fatalf("the next tick starts after it: %d", n)
	}
}
