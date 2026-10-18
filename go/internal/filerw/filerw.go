// Package filerw is file.read and file.write: the founder reads and edits a text file in an agent's worktree.
package filerw

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

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
	// beforeReplace runs between the checks and the replace: a test swaps a folder for a link there.
	beforeReplace func()
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

// target is the agent's worktree, opened as a root, and the path inside it that rel names. The root refuses, at every component and at the
// moment of use, any path that leaves it, so a folder swapped for a link between a check and a write leads nowhere.
func (e *Env) target(p map[string]any) (agent, rel string, root *os.Root, err error) {
	agent, _ = p["agent"].(string)
	rel, _ = p["path"].(string)
	if agent == "" {
		return "", "", nil, bad("agent is an agent's name", "agent")
	}
	if rel == "" || strings.HasPrefix(rel, "/") || strings.ContainsAny(rel, "\x00\\") {
		return "", "", nil, bad("path is relative to the agent's worktree", "path")
	}
	parts := strings.Split(rel, "/")
	for _, part := range parts {
		if part == ".." || part == "" || part == "." {
			return "", "", nil, bad("path has no .., empty or . part", "path")
		}
	}
	st, err := db.GetAgentState(e.DB, agent)
	if err != nil {
		return "", "", nil, err
	}
	wt := ""
	if st != nil {
		wt = st.Worktree.String
	}
	if i := strings.Index(wt, "::"); i >= 0 { // the column may hold `<repo>::<path>`
		wt = wt[i+2:]
	}
	if wt == "" {
		return "", "", nil, rpc.NotFoundError("no worktree for " + agent)
	}
	if root, err = os.OpenRoot(wt); err != nil {
		return "", "", nil, rpc.NotFoundError("the worktree of " + agent + " is gone")
	}
	cur := ""
	for _, part := range parts { // a link inside the worktree is refused too, not only one that leaves it
		cur = path.Join(cur, part)
		if fi, lerr := root.Lstat(cur); lerr == nil && fi.Mode()&os.ModeSymlink != 0 {
			root.Close()
			return "", "", nil, bad("path goes through a link: only what lies inside the worktree is used", "path")
		}
	}
	return agent, rel, root, nil
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

func etagOf(root *os.Root, rel string) (string, error) {
	f, err := root.Open(rel)
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
	_, rel, root, err := e.target(p)
	if err != nil {
		return nil, err
	}
	defer root.Close()
	fi, err := root.Lstat(rel)
	if err != nil {
		return nil, rpc.NotFoundError(rel + " is not there")
	}
	if !fi.Mode().IsRegular() {
		return nil, bad(rel+" is not a file", "path")
	}
	f, err := root.Open(rel)
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
	tag, err := etagOf(root, rel)
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
	agent, rel, root, err := e.target(p)
	if err != nil {
		return nil, err
	}
	defer root.Close()
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
	fi, err := root.Lstat(rel)
	switch {
	case err == nil && !fi.Mode().IsRegular():
		return nil, bad(rel+" is not a file", "path")
	case err == nil && fi.Size() > MaxBytes:
		return nil, rpc.Refused(rel+" is over 1 MiB: file.read gives only its start, so it is not written back", "too_big", "")
	case err == nil && etag == "":
		return nil, rpc.Refused(rel+" exists: read it and write with its etag", "stale", "")
	case err == nil:
		have, terr := etagOf(root, rel)
		if terr != nil || have != etag {
			return nil, rpc.Refused(rel+" changed since that etag: read it again", "stale", "")
		}
		mode = fi.Mode().Perm()
	case etag != "":
		return nil, rpc.Refused(rel+" is gone since that etag: read it again", "stale", "")
	default:
		if pi, perr := root.Stat(path.Dir(rel)); perr != nil || !pi.IsDir() {
			return nil, bad("the folder of "+rel+" is not there", "path")
		}
	}
	if e.beforeReplace != nil {
		e.beforeReplace()
	}
	if err := replace(root, rel, body, mode); err != nil {
		return nil, bad("cannot write "+rel+": "+err.Error(), "path")
	}
	nfi, err := root.Stat(rel)
	if err != nil {
		return nil, err
	}
	tag, err := etagOf(root, rel)
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

// replace writes body beside rel and renames it over rel, all inside root, so a reader sees the old file or the new.
func replace(root *os.Root, rel, body string, mode os.FileMode) error {
	tmp := path.Join(path.Dir(rel), fmt.Sprintf(".%s.%d.tmp", path.Base(rel), time.Now().UnixNano()))
	f, err := root.OpenFile(tmp, os.O_WRONLY|os.O_CREATE|os.O_EXCL, mode)
	if err != nil {
		return err
	}
	_, err = f.WriteString(body)
	if cerr := f.Chmod(mode); err == nil {
		err = cerr
	}
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err == nil {
		err = root.Rename(tmp, rel)
	}
	if err != nil {
		root.Remove(tmp)
	}
	return err
}
