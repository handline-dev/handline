package daemon

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/structured"
	"github.com/handline/handline/go/internal/transcript"
)

func startTranscript(t *testing.T, structuredAgents bool) (*Daemon, *transcript.Sink) {
	t.Helper()
	dir := t.TempDir()
	pre, err := store.Open(filepath.Join(dir, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	pre.Close()
	for _, a := range []string{"bob", "term"} {
		os.MkdirAll(filepath.Join(dir, "agents", a), 0o755)
		os.WriteFile(filepath.Join(dir, "agents", a, "agent.yaml"), nil, 0o644)
	}
	sink := &transcript.Sink{}
	cfg := Config{Home: dir, DBFile: filepath.Join(dir, "db.sqlite"), Auth: pidAuth{}}
	if structuredAgents {
		cfg.Transcript, cfg.Structured = sink, func(a string) bool { return a == "bob" }
	}
	d, err := Start(cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(d.Close)
	return d, sink
}

func TestHelloSaysTranscriptOnlyWhenTheDaemonServesIt(t *testing.T) {
	for _, on := range []bool{false, true} {
		d, _ := startTranscript(t, on)
		srvEnd := pipeTo(t, d, "k")
		r := srvEnd.ask(`{"jsonrpc":"2.0","id":2,"method":"hello","params":{"client":"t","protocol":1,"key":"k"}}`)
		if got := r["result"].(map[string]any)["capabilities"].(map[string]any)["transcript"]; got != on {
			t.Fatalf("serving %v: hello says %v", on, got)
		}
	}
}

func TestTranscriptEventsReachSubscribersAndThePageHoldsThem(t *testing.T) {
	d, sink := startTranscript(t, true)
	f := pipeTo(t, d, "k")
	if r := f.ask(`{"jsonrpc":"2.0","id":2,"method":"subscribe","params":{"topics":["transcript"],"agent":"bob"}}`); r["result"] == nil {
		t.Fatalf("%v", r)
	}
	sink.Emit("alice", structured.Event{Type: "assistant_text", Data: map[string]any{"text": "not wanted"}})
	sink.Emit("bob", structured.Event{Type: "assistant_text", Data: map[string]any{"text": "hello"}})
	ev := eventOf(t, f.next())
	data := ev["data"].(map[string]any)
	if ev["topic"] != "transcript" || ev["type"] != "transcript.event" || ev["agent"] != "bob" || data["kind"] != "text" || data["text"] != "hello" || data["seq"] != float64(1) {
		t.Fatalf("%v", ev)
	}
	page := f.ask(`{"jsonrpc":"2.0","id":3,"method":"transcript.events","params":{"agent":"bob"}}`)["result"].(map[string]any)
	evs := page["events"].([]any)
	if len(evs) != 1 || evs[0].(map[string]any)["text"] != "hello" || page["last_seq"] != float64(1) || page["gap"] != false {
		t.Fatalf("%v", page)
	}
	if r := f.ask(`{"jsonrpc":"2.0","id":4,"method":"transcript.events","params":{"agent":"term"}}`); r["error"].(map[string]any)["data"].(map[string]any)["gate"] != "unsupported" {
		t.Fatalf("%v", r)
	}
	if r := f.ask(`{"jsonrpc":"2.0","id":5,"method":"subscribe","params":{"topics":["transcript","chat"]}}`); r["error"] == nil {
		t.Fatalf("transcript beside another topic: %v", r)
	}
	a := pipeTo(t, d, "")
	if r := a.ask(`{"jsonrpc":"2.0","id":2,"method":"subscribe","params":{"topics":["transcript"]}}`); r["error"] == nil {
		t.Fatalf("an agent subscribed: %v", r)
	}
}

func TestWithoutTheSinkTranscriptEventsIsUnsupportedForEveryAgent(t *testing.T) {
	d, _ := startTranscript(t, false)
	f := pipeTo(t, d, "k")
	if r := f.ask(`{"jsonrpc":"2.0","id":4,"method":"transcript.events","params":{"agent":"bob"}}`); r["error"].(map[string]any)["data"].(map[string]any)["gate"] != "unsupported" {
		t.Fatalf("%v", r)
	}
}
