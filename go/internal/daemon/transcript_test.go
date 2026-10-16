package daemon

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/store"
)

func startTranscript(t *testing.T, transcripts bool) *Daemon {
	t.Helper()
	dir := t.TempDir()
	pre, err := store.Open(filepath.Join(dir, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	pre.Close()
	os.MkdirAll(filepath.Join(dir, "agents", "bob"), 0o755)
	os.WriteFile(filepath.Join(dir, "agents", "bob", "agent.yaml"), nil, 0o644)
	d, err := Start(Config{Home: dir, DBFile: filepath.Join(dir, "db.sqlite"), Auth: pidAuth{}, Transcripts: transcripts})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(d.Close)
	return d
}

func TestHelloSaysTranscriptOnlyWhenTheDaemonServesIt(t *testing.T) {
	for _, on := range []bool{false, true} {
		srvEnd := pipeTo(t, startTranscript(t, on), "k")
		r := srvEnd.ask(`{"jsonrpc":"2.0","id":2,"method":"hello","params":{"client":"t","protocol":1,"key":"k"}}`)
		if got := r["result"].(map[string]any)["capabilities"].(map[string]any)["transcript"]; got != on {
			t.Fatalf("serving %v: hello says %v", on, got)
		}
	}
}

func TestTranscriptEventsIsUnsupportedForEveryAgent(t *testing.T) {
	f := pipeTo(t, startTranscript(t, true), "k")
	if r := f.ask(`{"jsonrpc":"2.0","id":4,"method":"transcript.events","params":{"agent":"bob"}}`); r["error"].(map[string]any)["data"].(map[string]any)["gate"] != "unsupported" {
		t.Fatalf("%v", r)
	}
}
