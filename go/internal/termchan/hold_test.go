package termchan

import (
	"encoding/base64"
	"encoding/json"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/inputhold"
)

func holdRig(t *testing.T) rig {
	r := newRig(t)
	r.agent(t)
	r.e.Holds = &inputhold.Store{DB: r.e.DB, Changed: r.e.HoldersChanged, Now: r.e.Now}
	return r
}

func b64(s string) map[string]any {
	return map[string]any{"terminal": "%1", "data": base64.StdEncoding.EncodeToString([]byte(s))}
}

func TestKeystrokesAndAResizeAreTheInputHoldersAloneAndAReleaseGivesTheSizeBack(t *testing.T) {
	r := holdRig(t)
	pat, sam := person("pat"), person("sam")
	size := map[string]any{"terminal": "%1", "cols": json.Number("100"), "rows": json.Number("30")}

	if _, err := r.e.input(pat, b64("hi")); gate(t, err) != "input_holder" {
		t.Fatalf("the daemon holds the input: take it first, got %v", err)
	}
	if _, err := r.e.resize(pat, size); gate(t, err) != "input_holder" {
		t.Fatalf("the daemon holds the input: only a holder resizes, got %v", err)
	}
	if won, err := r.e.Holds.Take("ann", "person", "pat"); !won || err != nil {
		t.Fatalf("take %v %v", won, err)
	}
	if _, err := r.e.input(sam, b64("hi")); gate(t, err) != "input_holder" {
		t.Fatalf("another holds it, got %v", err)
	}
	if _, err := r.e.input(pat, b64("hi")); err != nil {
		t.Fatal(err)
	}
	if len(r.h.sent) != 1 || string(r.h.sent[0]) != "hi" {
		t.Fatalf("sent %q", r.h.sent)
	}
	if _, err := r.e.resize(pat, size); err != nil {
		t.Fatal(err)
	}
	if cols, rows, _ := r.h.Size("%1"); cols != 100 || rows != 30 {
		t.Fatalf("size %dx%d", cols, rows)
	}
	if err := r.e.Holds.Release("ann"); err != nil {
		t.Fatal(err)
	}
	if cols, rows, _ := r.h.Size("%1"); cols != 80 || rows != 24 {
		t.Fatalf("the size before the holder's is given back, got %dx%d", cols, rows)
	}
}

func TestInputRefusesWhatIsNotBytesOrCannotBeSentAndRecordsItsByteCountNeverTheBytes(t *testing.T) {
	r := holdRig(t)
	pat := person("pat")
	r.e.Holds.Take("ann", "person", "pat")
	for _, p := range []map[string]any{
		{"terminal": "%1", "data": "***"},
		{"terminal": "%1", "data": ""},
		{"terminal": "%1", "data": base64.StdEncoding.EncodeToString(make([]byte, MaxInput+1))},
		{"terminal": "%1"},
	} {
		if _, err := r.e.input(pat, p); err == nil {
			t.Fatalf("%v is not a call", p)
		}
	}
	if _, err := r.e.resize(pat, map[string]any{"terminal": "%1", "cols": json.Number("5"), "rows": json.Number("30")}); err == nil {
		t.Fatal("cols 5 is below the least")
	}
	if _, err := r.e.input(pat, b64("secret")); err != nil {
		t.Fatal(err)
	}
	var payload string
	if err := r.e.DB.QueryRow("SELECT payload FROM events WHERE agent = 'ann' AND kind = 'type'").Scan(&payload); err != nil {
		t.Fatal(err)
	}
	if want := `"bytes": 6`; !contains(payload, want) || contains(payload, "secret") {
		t.Fatalf("payload %s", payload)
	}
}

func TestAHoldThatWentIdleIsLostWhereverItIsFound(t *testing.T) {
	r := holdRig(t)
	now := time.Unix(1_000_000, 0)
	r.e.Holds.Now = func() time.Time { return now }
	r.e.Holds.IdleS = func() int64 { return 60 }
	r.e.Holds.Take("ann", "person", "pat")
	now = now.Add(61 * time.Second)
	if _, err := r.e.input(person("pat"), b64("late")); gate(t, err) != "input_holder" {
		t.Fatalf("an idle hold is the daemon's again, got %v", err)
	}
	if h, _ := r.e.holders("ann"); h[0]["kind"] != "daemon" {
		t.Fatalf("holders %v", h)
	}
}

func contains(s, sub string) bool {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return true
		}
	}
	return false
}
