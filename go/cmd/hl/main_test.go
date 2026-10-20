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

// rig is a built hl at <root>/go/hl beside <root>/bin, and a home.
// ctl, once daemon serves, is the socket each run inherits to reach it.
type rig struct {
	t              *testing.T
	root, home, hl string
	ctl            *os.File
}

var built struct {
	once sync.Once
	bin  string
	err  error
}

// TestMain removes the hl the rigs share once every test has run with it.
func TestMain(m *testing.M) {
	code := m.Run()
	if built.bin != "" {
		os.RemoveAll(filepath.Dir(built.bin))
	}
	os.Exit(code)
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
	r := &rig{t: t, root: root, home: filepath.Join(root, "h"), hl: filepath.Join(root, "go", "hl")}
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
		"TZ=UTC"}, env...)
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

func TestAnUnknownCommandIsRefused(t *testing.T) {
	r := newRig(t)
	for _, args := range [][]string{{}, {"agent", "list"}, {"doctor"}} {
		_, errs, code := r.run(nil, args...)
		if code != 2 || !strings.Contains(errs, "commands:") {
			t.Errorf("%v: %q (exit %d)", args, errs, code)
		}
	}
}

func TestNoVerbTakesAnArgumentItDoesNotParse(t *testing.T) {
	r := newRig(t)
	for _, verb := range []string{"say", "task", "progress", "read", "inbox", "withdraw", "team", "hr", "device", "service"} {
		out, errs, code := r.run(nil, verb, "--no-such-flag")
		if code != 2 || out != "" || !strings.Contains(errs, "arguments not understood") {
			t.Errorf("%s: %q %q %d", verb, out, errs, code)
		}
	}
}

func TestProgressRefusesWhatItDoesNotParse(t *testing.T) {
	r := newRig(t)
	for _, args := range [][]string{{"progress", "--help"}, {"progress", "a", "b"}} {
		out, errs, code := r.run(nil, args...)
		if code != 2 || !strings.Contains(errs, "arguments not understood") {
			t.Errorf("%v: %q %q %d", args, out, errs, code)
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

// A refused command line says what is wrong with it, then the verb's form and an example.
func TestARefusalSaysWhatIsWrongAndShowsTheForm(t *testing.T) {
	r := newRig(t)
	for _, c := range []struct {
		args []string
		want []string
	}{
		{[]string{"hr", "retire", "old.agent"}, []string{"(--why is missing", "usage: hl hr retire <agent> --why", "e.g.   hl hr retire"}},
		{[]string{"task", "new", "-x", "1h", "t"}, []string{"(unknown option -x)", "usage: hl task new --assign"}},
		{[]string{"task", "new", "--assign", "a", "-t", "soon", "t"}, []string{`(--timeout is a span like 30m or 2h, not "soon")`}},
		{[]string{"task", "show", "12"}, []string{`("12" is not a task: T<n>)`, "usage: hl task show T<n>"}},
		{[]string{"say", "bob", "two", "words"}, []string{"(too many words", "usage: hl say <target>"}},
		{[]string{"inbox", "--timeout"}, []string{"(--timeout needs a value after it)"}},
	} {
		_, errs, code := r.run(asAgent, c.args...)
		for _, w := range c.want {
			if code != 2 || !strings.Contains(errs, w) {
				t.Errorf("%v: %d %q, want %q", c.args, code, errs, w)
			}
		}
	}
}

// Every verb, and every task and hr subverb, has a form to show when its arguments are refused.
func TestEveryVerbHasAUsage(t *testing.T) {
	for verb := range commands {
		if _, ok := usages[verb]; !ok {
			t.Errorf("no usage for %s", verb)
		}
	}
	subs := []string{"add", "retire", "rename", "link", "unlink", "team", "context", "list", "withdraw"}
	for sub := range taskCmds {
		subs = append(subs, "task "+sub)
	}
	for _, s := range subs {
		key := s
		if !strings.HasPrefix(s, "task ") {
			key = "hr " + s
		}
		if _, ok := usages[key]; !ok {
			t.Errorf("no usage for %s", key)
		}
	}
}
