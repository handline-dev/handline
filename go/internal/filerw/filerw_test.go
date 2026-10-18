package filerw

import (
	"encoding/json"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
)

type auth struct{ agent string }

func (a auth) AgentOf(int) string       { return a.agent }
func (auth) FounderKeyOK(k string) bool { return k == "founder" }
func (auth) SessionsReadable() error    { return nil }

type rig struct {
	t    *testing.T
	wt   string
	srv  *rpc.Server
	env  *Env
	logs []string
}

func newRig(t *testing.T) *rig {
	t.Helper()
	wt, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	d, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	if _, err := d.Exec("INSERT INTO agent_state(agent, state, updated, worktree) VALUES('alice', 'IDLE', 0, ?)", "repo::"+wt); err != nil {
		t.Fatal(err)
	}
	r := &rig{t: t, wt: wt}
	s := rpc.NewServer(auth{}, "x", store.LatestVersion(), "local", func(string, ...any) {})
	s.DB = d
	r.env = &Env{DB: d, Log: func(f string, a ...any) { r.logs = append(r.logs, fmt.Sprintf(f, a...)) }}
	Register(s, r.env)
	r.srv = s
	return r
}

func (r *rig) put(rel, content string) {
	r.t.Helper()
	full := filepath.Join(r.wt, rel)
	os.MkdirAll(filepath.Dir(full), 0o755)
	if err := os.WriteFile(full, []byte(content), 0o644); err != nil {
		r.t.Fatal(err)
	}
}

// call opens a founder connection, says hello and makes the one call.
func (r *rig) call(method string, params map[string]any) map[string]any {
	r.t.Helper()
	cl, sv := net.Pipe()
	go func() { r.srv.Serve(rpc.NewLineTransport(sv), 0); sv.Close() }()
	defer cl.Close()
	cl.SetDeadline(time.Now().Add(10 * time.Second))
	enc, dec := json.NewEncoder(cl), json.NewDecoder(cl)
	hello := map[string]any{"client": "t", "protocol": 1, "key": "founder"}
	var m map[string]any
	for i, req := range []struct {
		method string
		params map[string]any
	}{{"hello", hello}, {method, params}} {
		m = nil
		if err := enc.Encode(map[string]any{"jsonrpc": "2.0", "id": i + 1, "method": req.method, "params": req.params}); err != nil {
			r.t.Fatal(err)
		}
		if err := dec.Decode(&m); err != nil {
			r.t.Fatal(err)
		}
	}
	return m
}

func result(t *testing.T, m map[string]any) map[string]any {
	t.Helper()
	if res, has := m["result"].(map[string]any); has {
		return res
	}
	t.Fatalf("refused: %v", m)
	return nil
}

func refusal(m map[string]any) string {
	e, _ := m["error"].(map[string]any)
	d, _ := e["data"].(map[string]any)
	if g, _ := d["gate"].(string); g != "" {
		return g
	}
	if f, _ := d["field"].(string); f != "" {
		return "bad:" + f
	}
	if c, _ := d["code"].(string); c != "" {
		return c
	}
	return fmt.Sprint(m)
}

func (r *rig) write(rel, text, etag string) map[string]any {
	return r.call("file.write", map[string]any{"agent": "alice", "path": rel, "text": text, "etag": etag, "request_id": fmt.Sprint(time.Now().UnixNano())})
}

func TestReadReturnsTheTextItsSizeAndAnEtag(t *testing.T) {
	r := newRig(t)
	r.put("docs/a.md", "# hi\n")
	res := result(t, r.call("file.read", map[string]any{"agent": "alice", "path": "docs/a.md"}))
	if res["text"] != "# hi\n" || res["size"] != float64(5) || res["truncated"] != false || res["etag"] == "" || res["mtime"] == "" {
		t.Fatalf("%v", res)
	}
	big := strings.Repeat("a", MaxBytes+10)
	r.put("big.md", big)
	res = result(t, r.call("file.read", map[string]any{"agent": "alice", "path": "big.md"}))
	if res["truncated"] != true || len(res["text"].(string)) != MaxBytes || res["size"] != float64(MaxBytes+10) {
		t.Fatalf("a long file is cut at the cap: truncated %v, %d chars", res["truncated"], len(res["text"].(string)))
	}
}

func TestAPathThatLeavesTheWorktreeOrIsNotTextIsRefused(t *testing.T) {
	r := newRig(t)
	outside := t.TempDir()
	os.WriteFile(outside+"/secret.md", []byte("secret"), 0o644)
	r.put("bin.md", "a\x00b")
	r.put("latin.md", "caf\xe9")
	os.Symlink(outside, filepath.Join(r.wt, "linkdir"))
	os.Symlink(outside+"/secret.md", filepath.Join(r.wt, "link.md"))
	for _, rel := range []string{"../x.md", "a/../../x.md", "/etc/passwd", outside + "/secret.md", "linkdir/secret.md", "link.md", "a\\b.md", "", "bin.md", "latin.md"} {
		for _, method := range []string{"file.read", "file.write"} {
			params := map[string]any{"agent": "alice", "path": rel, "text": "x", "etag": "", "request_id": "r" + rel + method}
			m := r.call(method, params)
			if m["error"] == nil {
				t.Errorf("%s %q was served: %v", method, rel, m)
			}
		}
	}
	if b, _ := os.ReadFile(outside + "/secret.md"); string(b) != "secret" {
		t.Fatalf("a write went through a link: %q", b)
	}
	if e := refusal(r.call("file.read", map[string]any{"agent": "alice", "path": "missing.md"})); e != "not_found" {
		t.Fatalf("a file that is not there: %s", e)
	}
	if e := refusal(r.call("file.read", map[string]any{"agent": "nobody", "path": "a.md"})); e == "" {
		t.Fatal("an agent with no worktree")
	}
}

func TestWriteReplacesWithTheRightEtagAndRefusesAStaleOne(t *testing.T) {
	r := newRig(t)
	r.put("notes.md", "one")
	read := result(t, r.call("file.read", map[string]any{"agent": "alice", "path": "notes.md"}))
	res := result(t, r.write("notes.md", "two", read["etag"].(string)))
	if b, _ := os.ReadFile(filepath.Join(r.wt, "notes.md")); string(b) != "two" || res["etag"] == read["etag"] || res["size"] != float64(3) {
		t.Fatalf("%q %v", b, res)
	}
	if g := refusal(r.write("notes.md", "three", read["etag"].(string))); g != "stale" {
		t.Fatalf("a stale etag: %s", g)
	}
	if g := refusal(r.write("notes.md", "three", "")); g != "stale" {
		t.Fatalf("an empty etag over an existing file: %s", g)
	}
	if b, _ := os.ReadFile(filepath.Join(r.wt, "notes.md")); string(b) != "two" {
		t.Fatalf("a refused write changed the file: %q", b)
	}
	if len(r.logs) != 1 || !strings.Contains(r.logs[0], "notes.md") || !strings.Contains(r.logs[0], "alice") {
		t.Fatalf("the write is logged once, with who and which file: %v", r.logs)
	}
}

func TestANewFileNeedsAnEmptyEtagAndAFolderThatIsThere(t *testing.T) {
	r := newRig(t)
	os.MkdirAll(filepath.Join(r.wt, "docs"), 0o755)
	result(t, r.write("docs/new.json", `{"a":1}`, ""))
	if g := refusal(r.write("docs/other.md", "x", "abc")); g != "stale" {
		t.Fatalf("an etag for a file that is not there: %s", g)
	}
	if g := refusal(r.write("nope/dir/x.md", "x", "")); g != "bad:path" {
		t.Fatalf("a folder that is not there: %s", g)
	}
	if g := refusal(r.write("docs/bad.json", "{oops", "")); g != "bad:text" {
		t.Fatalf("invalid JSON: %s", g)
	}
}

func TestWriteNeverTouchesWhatAnAgentsToolsRunOrObey(t *testing.T) {
	r := newRig(t)
	for _, rel := range []string{".git/config.json", ".claude/settings.json", ".claude/notes.md", ".github/workflows/x.json", "sub/.git/x.md", ".Claude/x.md",
		"settings.json", "app/settings.local.json", ".vscode/tasks.json", ".devcontainer/x.md", "tsconfig.json", ".mcp.json", "package.json", "hooks/pre.json", "run.sh", "src/main.go", "README"} {
		if g := refusal(r.write(rel, "x", "")); g != "denied" {
			t.Errorf("%s: %s", rel, g)
		}
		if _, err := os.Lstat(filepath.Join(r.wt, rel)); err == nil {
			t.Errorf("%s was written", rel)
		}
	}
}

func TestAFileOverTheCapIsNeverWrittenBackFromATruncatedRead(t *testing.T) {
	r := newRig(t)
	r.put("big.md", strings.Repeat("a", MaxBytes+10))
	read := result(t, r.call("file.read", map[string]any{"agent": "alice", "path": "big.md"}))
	if g := refusal(r.write("big.md", read["text"].(string), read["etag"].(string))); g != "too_big" {
		t.Fatalf("a truncated text written back: %s", g)
	}
	if fi, _ := os.Stat(filepath.Join(r.wt, "big.md")); fi.Size() != MaxBytes+10 {
		t.Fatalf("the file was cut to %d bytes", fi.Size())
	}
}

func TestAFolderSwappedForALinkAfterTheChecksLeadsNowhere(t *testing.T) {
	r := newRig(t)
	outside := t.TempDir()
	os.WriteFile(outside+"/a.md", []byte("outside"), 0o644)
	r.put("docs/a.md", "inside")
	read := result(t, r.call("file.read", map[string]any{"agent": "alice", "path": "docs/a.md"}))
	r.env.beforeReplace = func() {
		os.Rename(filepath.Join(r.wt, "docs"), filepath.Join(r.wt, "docs.old"))
		os.Symlink(outside, filepath.Join(r.wt, "docs"))
	}
	if m := r.write("docs/a.md", "pwned", read["etag"].(string)); m["error"] == nil {
		t.Fatalf("a write went through the swapped folder: %v", m)
	}
	if b, _ := os.ReadFile(outside + "/a.md"); string(b) != "outside" {
		t.Fatalf("the file outside the worktree changed: %q", b)
	}
	if left, _ := filepath.Glob(outside + "/*.tmp"); len(left) != 0 {
		t.Fatalf("a temp file landed outside: %v", left)
	}
	if b, _ := os.ReadFile(filepath.Join(r.wt, "docs.old", "a.md")); string(b) != "inside" {
		t.Fatalf("the moved folder changed: %q", b)
	}
}
