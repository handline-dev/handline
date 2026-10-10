package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"

	"github.com/handline/handline/go/internal/pairsock"
)

// rig is a built hl at <root>/go/hl beside <root>/bin, a fake python that prints what it was run with, and a home.
// ctl, once daemon serves, is the socket each run inherits to reach it.
type rig struct {
	t                  *testing.T
	root, home, hl, py string
	ctl                *os.File
}

var built struct {
	once sync.Once
	bin  string
	err  error
}

func newRig(t *testing.T) *rig {
	t.Helper()
	root, err := os.MkdirTemp("", "hl")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(root) })
	built.once.Do(func() {
		dir, err := os.MkdirTemp("", "hlbin")
		if err != nil {
			built.err = err
			return
		}
		built.bin = filepath.Join(dir, "hl")
		if out, err := exec.Command("go", "build", "-o", built.bin, ".").CombinedOutput(); err != nil {
			built.err = fmt.Errorf("go build: %v\n%s", err, out)
		}
	})
	if built.err != nil {
		t.Fatal(built.err)
	}
	r := &rig{t: t, root: root, home: filepath.Join(root, "h"), hl: filepath.Join(root, "go", "hl"), py: filepath.Join(root, "py")}
	for _, d := range []string{r.home, filepath.Join(root, "go"), filepath.Join(root, "bin")} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	b, err := os.ReadFile(built.bin)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(r.hl, b, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(r.py, []byte("#!/bin/sh\necho \"python $*\"\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	return r
}

// run is hl with args under env, nothing inherited but PATH: (stdout, stderr, exit code).
func (r *rig) run(env []string, args ...string) (string, string, int) {
	return r.runIn("", env, args...)
}

// runIn is run with stdin.
func (r *rig) runIn(stdin string, env []string, args ...string) (string, string, int) {
	cmd := exec.Command(r.hl, args...)
	cmd.Dir = r.root
	cmd.Stdin = strings.NewReader(stdin)
	cmd.Env = append([]string{"PATH=" + os.Getenv("PATH"), "HANDLINE_TEST_ROOT=" + r.root, "HANDLINE_HOME=" + r.home,
		"HANDLINE_HL_PYTHON=" + r.py, "TZ=UTC"}, env...)
	if r.ctl != nil {
		cmd.ExtraFiles = []*os.File{r.ctl}
		cmd.Env = append(cmd.Env, "HANDLINE_DAEMON_FD=3")
	}
	var out, errb strings.Builder
	cmd.Stdout, cmd.Stderr = &out, &errb
	err := cmd.Run()
	code := 0
	if ee, ok := err.(*exec.ExitError); ok {
		code = ee.ExitCode()
	} else if err != nil {
		r.t.Fatal(err)
	}
	return out.String(), errb.String(), code
}

func TestWhatIsNotPortedRunsPythonWithTheSameArgv(t *testing.T) {
	r := newRig(t)
	real, err := filepath.EvalSymlinks(r.root)
	if err != nil {
		t.Fatal(err)
	}
	entry := filepath.Join(real, "bin", "_ocp_entry.py")
	for _, args := range [][]string{{}, {"inbox", "--all"}, {"progress", "--help"}, {"progress", "a", "b"}} {
		out, _, code := r.run(nil, args...)
		want := strings.TrimSpace("python " + entry + " " + strings.Join(args, " "))
		if code != 0 || strings.TrimSpace(out) != want {
			t.Errorf("%v: %q (exit %d), want %q", args, out, code, want)
		}
	}
}

func TestProgressNamesNoOneWithoutAnAgent(t *testing.T) {
	r := newRig(t)
	out, errs, code := r.run(nil, "progress", "x")
	if code != 1 || out != "" || errs != "cannot tell who is writing: OCP_AGENT is unset\n" {
		t.Fatalf("%q %q %d", out, errs, code)
	}
}

// daemon answers the rig's runs over socketpairs, no socket file being bound: each method answers its result (an
// object, `{}` when none is given), or its error when the result starts with "error:". calls is every message
// received, hello included.
func (r *rig) daemon(results map[string]string) (calls func() []map[string]any) {
	ln := pairsock.NewListener()
	r.t.Cleanup(func() { ln.Close() })
	ctl, err := ln.Control()
	if err != nil {
		r.t.Fatal(err)
	}
	r.ctl = ctl
	var mu sync.Mutex
	var got []map[string]any
	go func() {
		for {
			conn, err := ln.Accept()
			if err != nil {
				return
			}
			sc := bufio.NewScanner(conn)
			for sc.Scan() {
				var m map[string]any
				_ = json.Unmarshal(sc.Bytes(), &m)
				mu.Lock()
				got = append(got, m)
				mu.Unlock()
				id, _ := json.Marshal(m["id"])
				result, _ := m["method"].(string)
				answer := `"result":{}`
				if res, ok := results[result]; ok && strings.HasPrefix(res, "error:") {
					answer = `"error":{"code":-32000,"message":` + strconv.Quote(res[6:]) + `,"data":{"code":"refused"}}`
				} else if ok {
					var flat bytes.Buffer
					if err := json.Compact(&flat, []byte(res)); err != nil {
						panic(err)
					}
					answer = `"result":` + flat.String()
				}
				conn.Write([]byte(`{"jsonrpc":"2.0","id":` + string(id) + `,` + answer + "}\n"))
			}
			conn.Close()
		}
	}()
	return func() []map[string]any {
		mu.Lock()
		defer mu.Unlock()
		var out []map[string]any
		for _, m := range got {
			if m["method"] != "hello" {
				out = append(out, m)
			}
		}
		return out
	}
}

func TestProgressWritesAndShowsTheNoteThroughTheDaemon(t *testing.T) {
	r := newRig(t)
	calls := r.daemon(map[string]string{"task.progress": `{"said":"progress kept on #7"}`,
		"progress.get": `{"note":"done: a\u001b[31m"}`})
	agent := []string{"HANDLINE_AGENT=a.one"}
	if out, errs, code := r.run(agent, "progress", "done: a"); code != 0 || out != "progress kept on #7\n" || errs != "" {
		t.Fatalf("write: %q %q %d", out, errs, code)
	}
	if out, _, code := r.run(agent, "progress"); code != 0 || out != "done: a\n" {
		t.Fatalf("show: %q %d", out, code)
	}
	got := calls()
	if len(got) != 2 || got[0]["method"] != "task.progress" || got[1]["method"] != "progress.get" {
		t.Fatalf("calls %v", got)
	}
	if p := got[0]["params"].(map[string]any); p["note"] != "done: a" || p["request_id"] == nil {
		t.Fatalf("params %v", p)
	}
}
