// Package taskresult keeps what a task's end carries beyond its evidence: the commit it was done at and the screenshots that show it. The
// screenshots are copied under the daemon's home (shots/T<n>/<k>.png, outside any repo); the commit and the list are a meta record, so the
// database has no new column.
package taskresult

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
)

const (
	MaxShots = 6
	MaxShot  = 10 << 20  // bytes of one screenshot
	MaxChunk = 512 << 10 // bytes of file in one task.shot reply
)

var pngMagic = []byte("\x89PNG\r\n\x1a\n")

// Shot is one stored screenshot of a task, numbered from 1.
type Shot struct {
	N     int   `json:"n"`
	Bytes int64 `json:"bytes"`
}

// Result is what a finished task carries. Commit is the full sha it was done at, OnMain whether main reaches it; NoCommit and NoShot are the
// reasons given when there is none.
type Result struct {
	Commit   string `json:"commit,omitempty"`
	OnMain   bool   `json:"on_main,omitempty"`
	NoCommit string `json:"no_commit,omitempty"`
	Shots    []Shot `json:"shots,omitempty"`
	NoShot   string `json:"no_shot,omitempty"`
}

// Empty is whether there is nothing to show.
func (r Result) Empty() bool {
	return r.Commit == "" && r.NoCommit == "" && len(r.Shots) == 0 && r.NoShot == ""
}

func resultKey(tid int64) string { return fmt.Sprintf("task-result:%d", tid) }
func uiKey(tid int64) string     { return fmt.Sprintf("task-ui:%d", tid) }

// Save keeps the result of task tid.
func Save(q db.Q, tid int64, r Result) error {
	b, err := json.Marshal(r)
	if err != nil {
		return err
	}
	return db.SetMeta(q, resultKey(tid), string(b))
}

// Get is the result of task tid; the zero Result for a task with none.
func Get(q db.Q, tid int64) (Result, error) {
	var r Result
	v, ok, err := db.Meta(q, resultKey(tid))
	if err != nil || !ok {
		return r, err
	}
	if json.Unmarshal([]byte(v), &r) != nil {
		return Result{}, nil
	}
	return r, nil
}

// MarkUI records that task tid shows something on a screen, so it cannot be done without a screenshot.
func MarkUI(q db.Q, tid int64) error { return db.SetMeta(q, uiKey(tid), "1") }

// IsUI is whether task tid was opened as a ui task.
func IsUI(q db.Q, tid int64) (bool, error) {
	_, ok, err := db.Meta(q, uiKey(tid))
	return ok, err
}

// Dir is where the screenshots of task tid are kept.
func Dir(home string, tid int64) string { return filepath.Join(home, "shots", fmt.Sprintf("T%d", tid)) }

func shotFile(home string, tid int64, n int) string {
	return filepath.Join(Dir(home, tid), fmt.Sprintf("%d.png", n))
}

// Stage reads the PNG files at paths and writes them to a staging folder beside the task's, answering what will be kept and the function
// that puts them in place. Nothing of an earlier result is touched until finalize runs, which the caller does once the task's end is
// committed: a done that fails leaves the shots it had. A path must be absolute, name a regular PNG file of at most MaxShot bytes and, when
// roots is not nil, sit under one of them (the file is read through the daemon's own handle, with links resolved first, so a link cannot
// lead it elsewhere).
func Stage(home string, tid int64, paths, roots []string) (shots []Shot, finalize func() error, err error) {
	if len(paths) > MaxShots {
		return nil, nil, bad(fmt.Sprintf("a task carries at most %d screenshots", MaxShots), "shots")
	}
	datas := make([][]byte, len(paths))
	for i, p := range paths {
		if datas[i], err = readPNG(p, roots); err != nil {
			return nil, nil, err
		}
	}
	dir := Dir(home, tid)
	stage := dir + ".stage"
	if err := os.RemoveAll(stage); err != nil {
		return nil, nil, err
	}
	if len(datas) == 0 {
		return nil, nil, nil
	}
	if err := os.MkdirAll(stage, 0o700); err != nil {
		return nil, nil, err
	}
	shots = make([]Shot, len(datas))
	for i, b := range datas {
		if err := os.WriteFile(filepath.Join(stage, fmt.Sprintf("%d.png", i+1)), b, 0o600); err != nil {
			os.RemoveAll(stage)
			return nil, nil, err
		}
		shots[i] = Shot{N: i + 1, Bytes: int64(len(b))}
	}
	return shots, func() error {
		if err := os.RemoveAll(dir); err != nil {
			return err
		}
		return os.Rename(stage, dir)
	}, nil
}

func bad(message, field string) *rpc.Error {
	return rpc.BadRequestError(message, field, rpc.InvalidParams)
}

// under is whether path is inside one of the folders, links resolved on both sides.
func under(path string, roots []string) bool {
	real, err := filepath.EvalSymlinks(path)
	if err != nil {
		return false
	}
	for _, r := range roots {
		if root, err := filepath.EvalSymlinks(r); err == nil && strings.HasPrefix(real, root+string(filepath.Separator)) {
			return true
		}
	}
	return false
}

func readPNG(p string, roots []string) ([]byte, error) {
	if !filepath.IsAbs(p) {
		return nil, bad("a screenshot is named by its absolute path: "+p, "shots")
	}
	if roots != nil && !under(p, roots) {
		return nil, bad(p+" is outside the places a screenshot may be taken from: the agent's worktree, its folder and the scratch folder", "shots")
	}
	f, err := os.Open(p)
	if err != nil {
		return nil, bad("cannot read the screenshot "+p, "shots")
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil || !st.Mode().IsRegular() {
		return nil, bad(p+" is not a file", "shots")
	}
	if st.Size() > MaxShot {
		return nil, bad(fmt.Sprintf("%s is over %d MiB", p, MaxShot>>20), "shots")
	}
	b, err := io.ReadAll(io.LimitReader(f, MaxShot+1))
	if err != nil || int64(len(b)) > MaxShot {
		return nil, bad("cannot read the screenshot "+p, "shots")
	}
	if !bytes.HasPrefix(b, pngMagic) {
		return nil, bad(p+" is not a PNG", "shots")
	}
	return b, nil
}

// Register adds task.shot to the server's table.
func Register(s *rpc.Server, home string, q db.Q) {
	s.Register("task.shot", rpc.Method{Role: "member", Handler: func(_ *rpc.Conn, p map[string]any) (any, error) {
		return serve(home, q, p)
	}})
}

func serve(home string, q db.Q, p map[string]any) (any, error) {
	tid, ok := rpc.AsInt(p["task"])
	if !ok {
		return nil, bad("task is a task number", "task")
	}
	n, ok := rpc.AsInt(p["n"])
	if !ok || n < 1 {
		return nil, bad("n is the screenshot's number, from 1", "n")
	}
	var offset int64
	if v := p["offset"]; v != nil {
		if offset, ok = rpc.AsInt(v); !ok || offset < 0 {
			return nil, bad("offset is where the chunk starts in the file", "offset")
		}
	}
	res, err := Get(q, tid)
	if err != nil {
		return nil, err
	}
	if n > int64(len(res.Shots)) { // only a screenshot the task's end recorded is served
		e := rpc.NotFoundError(fmt.Sprintf("T%d has no screenshot %d", tid, n))
		e.Data = map[string]any{"what": "shot", "task": tid, "n": n}
		return nil, e
	}
	f, err := os.Open(shotFile(home, tid, int(n)))
	if err != nil {
		e := rpc.NotFoundError(fmt.Sprintf("the screenshot %d of T%d is no longer kept", n, tid))
		e.Data = map[string]any{"what": "shot", "task": tid, "n": n}
		return nil, e
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return nil, err
	}
	if offset > st.Size() {
		return nil, bad("offset is within the file", "offset")
	}
	buf := make([]byte, min(int64(MaxChunk), st.Size()-offset))
	if _, err := f.ReadAt(buf, offset); err != nil && err != io.EOF {
		return nil, err
	}
	return map[string]any{"mime": "image/png", "bytes": st.Size(), "offset": offset, "data": base64.StdEncoding.EncodeToString(buf),
		"done": offset+int64(len(buf)) >= st.Size()}, nil
}
