package daemon

import (
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/attachments"
)

// onePNG is a 1x1 PNG.
var onePNG, _ = base64.StdEncoding.DecodeString("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

func TestAMemoCarriesAnImageAndAMarkdownFileThroughTaskGet(t *testing.T) {
	d, agents := tellDaemon(t, true)
	doing(t, d, "bob")
	src := t.TempDir()
	png, md := filepath.Join(src, "mock.png"), filepath.Join(src, "plan.md")
	os.WriteFile(png, onePNG, 0o644)
	os.WriteFile(md, []byte("# Plan\n\nthree steps\n"), 0o644)
	f := pipeTo(t, d, "k")
	files, _ := json.Marshal([]string{png, md})
	result(t, f.ask(`{"jsonrpc":"2.0","id":2,"method":"task.memo.add","params":{"task":1,"text":"@bob the proposal","files":`+string(files)+`,"request_id":"m1"}}`))

	task := result(t, f.ask(`{"jsonrpc":"2.0","id":3,"method":"task.get","params":{"task":1}}`))
	memo := task["memos"].([]any)[0].(map[string]any)
	got := memo["files"].([]any)
	if len(got) != 2 {
		t.Fatalf("%v", memo)
	}
	for i, want := range []struct {
		kind, name, mime string
		bytes            int
	}{{"image", "mock.png", "image/png", len(onePNG)}, {"markdown", "plan.md", "text/markdown", 20}} {
		m := got[i].(map[string]any)
		if m["kind"] != want.kind || m["name"] != want.name || m["mime"] != want.mime || m["bytes"] != float64(want.bytes) || m["path"] != nil {
			t.Fatalf("file %d: %v", i, m)
		}
	}
	// read back the way the apps read an attached image
	id := got[1].(map[string]any)["id"].(string)
	back := result(t, f.ask(`{"jsonrpc":"2.0","id":4,"method":"attachment.get","params":{"id":"`+id+`"}}`))
	if text, _ := base64.StdEncoding.DecodeString(back["data"].(string)); string(text) != "# Plan\n\nthree steps\n" || back["mime"] != "text/markdown" {
		t.Fatalf("%v", back)
	}
	// the copies are the daemon's, under its home; the folder they came from holds only what it had
	if entries, _ := os.ReadDir(src); len(entries) != 2 {
		t.Fatalf("%v", entries)
	}
	kept, _ := filepath.Glob(filepath.Join(d.cfg.Home, "attachments", "*.md"))
	if len(kept) != 1 {
		t.Fatalf("%v", kept)
	}
	// a memo naming the holder is typed at once, with a line per file it can read
	if said := agents.said("bob"); !strings.Contains(said, "the proposal") || !strings.Contains(said, "[file 2: "+kept[0]+" (text/markdown, 20 B)]") {
		t.Fatalf("%q", said)
	}
}

func TestAMemoFileOfAnotherTypeIsRefused(t *testing.T) {
	d, _ := tellDaemon(t, true)
	doing(t, d, "bob")
	src := t.TempDir()
	txt, fake := filepath.Join(src, "notes.txt"), filepath.Join(src, "fake.png")
	os.WriteFile(txt, []byte("hi"), 0o644)
	os.WriteFile(fake, []byte("# not a png"), 0o644)
	f := pipeTo(t, d, "k")
	for i, p := range []string{txt, fake, "relative.md"} {
		files, _ := json.Marshal([]string{p})
		r := f.ask(`{"jsonrpc":"2.0","id":2,"method":"task.memo.add","params":{"task":1,"text":"x","files":` + string(files) + `,"request_id":"r` + string(rune('0'+i)) + `"}}`)
		e, _ := r["error"].(map[string]any)
		if data, _ := e["data"].(map[string]any); data["field"] != "files" {
			t.Fatalf("%s: %v", p, r)
		}
	}
	task := result(t, f.ask(`{"jsonrpc":"2.0","id":3,"method":"task.get","params":{"task":1}}`))
	if memos := task["memos"].([]any); len(memos) != 0 {
		t.Fatalf("a refused memo was kept: %v", memos)
	}
}

func TestAMemosFileIsHeldWhileItsTaskIsOpenAndNotOnceTheMemoIsDeleted(t *testing.T) {
	d, _ := tellDaemon(t, true)
	doing(t, d, "bob")
	md := filepath.Join(t.TempDir(), "plan.md")
	os.WriteFile(md, []byte("# plan"), 0o644)
	f := pipeTo(t, d, "k")
	files, _ := json.Marshal([]string{md})
	result(t, f.ask(`{"jsonrpc":"2.0","id":2,"method":"task.memo.add","params":{"task":1,"text":"x","files":`+string(files)+`,"request_id":"m1"}}`))
	task := result(t, f.ask(`{"jsonrpc":"2.0","id":3,"method":"task.get","params":{"task":1}}`))
	id := task["memos"].([]any)[0].(map[string]any)["files"].([]any)[0].(map[string]any)["id"].(string)
	if held, err := attachments.Held(d.db, 12345); err != nil || held[id] != 12345 {
		t.Fatalf("held from %d (%v), want now", held[id], err)
	}
	result(t, f.ask(`{"jsonrpc":"2.0","id":4,"method":"task.memo.delete","params":{"task":1,"memo":1,"request_id":"m2"}}`))
	if held, _ := attachments.Held(d.db, 12345); len(held) != 0 {
		t.Fatalf("a deleted memo's file is still held: %v", held)
	}
}

// the founder's memo is typed at once to the holder, which cuts its text but keeps each file line whole on its own line
func TestTheFoundersMemoKeepsALinePerFileWhenTypedToTheHolder(t *testing.T) {
	d, _ := tellDaemon(t, true)
	doing(t, d, "bob")
	src := t.TempDir()
	png, md := filepath.Join(src, "mock.png"), filepath.Join(src, "plan.md")
	os.WriteFile(png, onePNG, 0o644)
	os.WriteFile(md, []byte("# Plan\n\nthree steps\n"), 0o644)
	f := pipeTo(t, d, "k")
	files, _ := json.Marshal([]string{png, md})
	result(t, f.ask(`{"jsonrpc":"2.0","id":2,"method":"task.memo.add","params":{"task":1,"text":"the proposal `+strings.Repeat("x", 500)+`","files":`+string(files)+`,"request_id":"m1"}}`))
	kept, _ := filepath.Glob(filepath.Join(d.cfg.Home, "attachments", "*.*"))
	if len(kept) != 2 {
		t.Fatalf("%v", kept)
	}
	var said string // a long body is typed cut, with `hl read` for the whole, which the request keeps
	if err := d.db.QueryRow("SELECT body FROM requests WHERE target = 'bob' AND body LIKE 'T1 memo added%'").Scan(&said); err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"T1 memo added by human: the proposal xx", "\n[file 1: ", "\n[file 2: "} {
		if !strings.Contains(said, want) {
			t.Fatalf("no %q in the told memo: %q", want, said)
		}
	}
	for _, p := range kept {
		if !strings.Contains(said, p+" (") {
			t.Fatalf("%s is not whole in the told memo: %q", p, said)
		}
	}
}
