// Package filerw is file.read and file.write: the founder reads and edits a text file in an agent's worktree.
package filerw

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/handline/handline/go/internal/atomicfile"
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
)

// MaxBytes is the most a read returns and a write takes.
const MaxBytes = 1 << 20

// Env is what the two methods read: the board for the agent's worktree, and the log a write is recorded in.
type Env struct {
	DB   db.Q
	Log  func(format string, args ...any)
	lock sync.Mutex // one write at a time, so the etag check and the replace are one step
}

// Register adds file.read and file.write: the founder's, which a paired device is.
func Register(s *rpc.Server, e *Env) {
	s.Register("file.read", rpc.Method{Role: "founder", Handler: e.read})
	s.Register("file.write", rpc.Method{Role: "founder", Write: true, Handler: e.write})
}

// deniedDirs and deniedNames are what a write never touches: what an agent's tools run or are allowed by.
var (
	deniedDirs  = []string{".git", ".claude", ".github", ".husky", ".codex", ".cursor", ".vscode", ".devcontainer", "hooks"}
	deniedNames = []string{"settings.json", "settings.local.json", ".mcp.json", "mcp.json", "hooks.json", "package.json", ".claude.json",
		"tsconfig.json", "turbo.json", "deno.json", "composer.json", "lefthook.json"}
)

func bad(message, field string) error { return rpc.BadRequestError(message, field, rpc.InvalidParams) }

func denied(message string) error { return rpc.Refused(message, "denied", "") }

// target is the agent's worktree and the file inside it that rel names, no link on the way.
func (e *Env) target(p map[string]any) (agent, rel, root, full string, err error) {
	agent, _ = p["agent"].(string)
	rel, _ = p["path"].(string)
	if agent == "" {
		return "", "", "", "", bad("agent is an agent's name", "agent")
	}
	if rel == "" || strings.HasPrefix(rel, "/") || strings.ContainsAny(rel, "\x00\\") {
		return "", "", "", "", bad("path is relative to the agent's worktree", "path")
	}
	parts := strings.Split(rel, "/")
	for _, part := range parts {
		if part == ".." || part == "" || part == "." {
			return "", "", "", "", bad("path has no .., empty or . part", "path")
		}
	}
	st, err := db.GetAgentState(e.DB, agent)
	if err != nil {
		return "", "", "", "", err
	}
	wt := ""
	if st != nil {
		wt = st.Worktree.String
	}
	if i := strings.Index(wt, "::"); i >= 0 { // the column may hold `<repo>::<path>`
		wt = wt[i+2:]
	}
	if wt == "" {
		return "", "", "", "", rpc.NotFoundError("no worktree for " + agent)
	}
	root, err = filepath.EvalSymlinks(wt)
	if err != nil {
		return "", "", "", "", rpc.NotFoundError("the worktree of " + agent + " is gone")
	}
	full = root
	for _, part := range parts {
		full = filepath.Join(full, part)
		if fi, lerr := os.Lstat(full); lerr == nil && fi.Mode()&os.ModeSymlink != 0 {
			return "", "", "", "", bad("path goes through a link: only what lies inside the worktree is used", "path")
		}
	}
	return agent, rel, root, full, nil
}

// text is whether b, cut at a rune boundary when it was cut, is text a person edits.
func text(b []byte, cut bool) (string, bool) {
	if cut {
		for i := 0; i < utf8.UTFMax && len(b) > 0 && !utf8.Valid(b); i++ {
			b = b[:len(b)-1]
		}
	}
	if !utf8.Valid(b) || strings.IndexByte(string(b), 0) >= 0 {
		return "", false
	}
	return string(b), true
}

func etagOf(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

func stamp(t time.Time) string { return t.UTC().Format("2006-01-02T15:04:05Z") }

func (e *Env) read(_ *rpc.Conn, p map[string]any) (any, error) {
	_, rel, _, full, err := e.target(p)
	if err != nil {
		return nil, err
	}
	fi, err := os.Lstat(full)
	if err != nil {
		return nil, rpc.NotFoundError(rel + " is not there")
	}
	if !fi.Mode().IsRegular() {
		return nil, bad(rel+" is not a file", "path")
	}
	f, err := os.Open(full)
	if err != nil {
		return nil, bad("cannot read "+rel, "path")
	}
	defer f.Close()
	b, err := io.ReadAll(io.LimitReader(f, MaxBytes))
	if err != nil {
		return nil, bad("cannot read "+rel, "path")
	}
	truncated := fi.Size() > MaxBytes
	s, ok := text(b, truncated)
	if !ok {
		return nil, bad(rel+" is not text", "path")
	}
	tag, err := etagOf(full)
	if err != nil {
		return nil, bad("cannot read "+rel, "path")
	}
	return map[string]any{"text": s, "size": fi.Size(), "truncated": truncated, "mtime": stamp(fi.ModTime()), "etag": tag}, nil
}

func checkWritable(rel string) error {
	parts := strings.Split(strings.ToLower(rel), "/")
	for _, dir := range parts[:len(parts)-1] {
		for _, d := range deniedDirs {
			if dir == d {
				return denied(fmt.Sprintf("%s is under %s/: a file there changes what an agent may run", rel, d))
			}
		}
	}
	name := parts[len(parts)-1]
	for _, n := range deniedNames {
		if name == n {
			return denied(rel + " is a settings or hooks file an agent's tools read")
		}
	}
	if !strings.HasSuffix(name, ".md") && !strings.HasSuffix(name, ".json") {
		return denied(rel + " is not a .md or .json file: only those are written")
	}
	return nil
}

func (e *Env) write(c *rpc.Conn, p map[string]any) (any, error) {
	agent, rel, _, full, err := e.target(p)
	if err != nil {
		return nil, err
	}
	body, ok := p["text"].(string)
	if !ok {
		return nil, bad("text is the whole new content", "text")
	}
	etag, ok := p["etag"].(string)
	if !ok {
		return nil, bad("etag is the etag read, or empty for a new file", "etag")
	}
	if len(body) > MaxBytes || !utf8.ValidString(body) || strings.IndexByte(body, 0) >= 0 {
		return nil, bad(fmt.Sprintf("text is UTF-8 text of at most %d bytes", MaxBytes), "text")
	}
	if err := checkWritable(rel); err != nil {
		return nil, err
	}
	if strings.HasSuffix(strings.ToLower(rel), ".json") && !json.Valid([]byte(body)) {
		return nil, bad("text is not valid JSON", "text")
	}
	e.lock.Lock()
	defer e.lock.Unlock()
	mode := os.FileMode(0o644)
	fi, err := os.Lstat(full)
	switch {
	case err == nil && !fi.Mode().IsRegular():
		return nil, bad(rel+" is not a file", "path")
	case err == nil && fi.Size() > MaxBytes:
		return nil, rpc.Refused(rel+" is over 1 MiB: file.read gives only its start, so it is not written back", "too_big", "")
	case err == nil && etag == "":
		return nil, rpc.Refused(rel+" exists: read it and write with its etag", "stale", "")
	case err == nil:
		have, terr := etagOf(full)
		if terr != nil || have != etag {
			return nil, rpc.Refused(rel+" changed since that etag: read it again", "stale", "")
		}
		mode = fi.Mode().Perm()
	case etag != "":
		return nil, rpc.Refused(rel+" is gone since that etag: read it again", "stale", "")
	default:
		if pi, perr := os.Stat(filepath.Dir(full)); perr != nil || !pi.IsDir() {
			return nil, bad("the folder of "+rel+" is not there", "path")
		}
	}
	if err := atomicfile.Write(full, body); err != nil {
		return nil, err
	}
	os.Chmod(full, mode)
	nfi, err := os.Stat(full)
	if err != nil {
		return nil, err
	}
	tag, err := etagOf(full)
	if err != nil {
		return nil, err
	}
	if e.Log != nil {
		who := c.Caller.Actor()
		if dev, _ := c.Caller.Info["name"].(string); dev != "" {
			who += " (" + dev + ")"
		}
		e.Log("  file.write by %s: %s in the worktree of %s (%d bytes)", who, rel, agent, len(body))
	}
	return map[string]any{"size": nfi.Size(), "mtime": stamp(nfi.ModTime()), "etag": tag}, nil
}
