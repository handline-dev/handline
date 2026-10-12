package taskwrites

import (
	"encoding/base64"
	"fmt"
	"os"
	osexec "os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/taskresult"
)

// pngFile is a file with a PNG's first bytes.
func pngFile(t *testing.T, dir, name string, size int) string {
	t.Helper()
	p := filepath.Join(dir, name)
	b := append([]byte("\x89PNG\r\n\x1a\n"), make([]byte, size)...)
	if err := os.WriteFile(p, b, 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

func newResultRig(t *testing.T) (home, repo, onMain, offMain string, done func(agent, rid string, task int, params string) map[string]any, ui func(task int)) {
	t.Helper()
	d, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	home = t.TempDir()
	for _, a := range []string{"w", "boss"} {
		os.MkdirAll(filepath.Join(home, "agents", a), 0o755)
		os.WriteFile(filepath.Join(home, "agents", a, "agent.yaml"), nil, 0o644)
	}
	repo = t.TempDir()
	git := func(args ...string) string {
		t.Helper()
		cmd := osexec.Command("git", append([]string{"-c", "user.name=t", "-c", "user.email=t@example.com"}, args...)...)
		cmd.Dir = repo
		out, err := cmd.CombinedOutput()
		if err != nil {
			t.Fatalf("git %v: %s", args, out)
		}
		return strings.TrimSpace(string(out))
	}
	git("init", "-q", "-b", "main")
	git("commit", "-q", "--allow-empty", "-m", "on main")
	onMain = git("rev-parse", "HEAD")
	git("checkout", "-q", "-b", "side")
	git("commit", "-q", "--allow-empty", "-m", "off main")
	offMain = git("rev-parse", "HEAD")
	if _, err := d.Exec("INSERT INTO tasks(id, title, owner, opened_by, created_at, expects) VALUES " +
		"(1, 'plain', 'w', 'boss', 1, 'report'), (2, 'screen', 'w', 'boss', 1, 'report'), (3, 'screen2', 'w', 'boss', 1, 'report'), " +
		"(4, 'screen3', 'w', 'boss', 1, 'report'), (5, 'needs sha', 'w', 'boss', 1, 'sha'), (6, 'screen4', 'w', 'boss', 1, 'report')"); err != nil {
		t.Fatal(err)
	}
	f := &fakeTeller{}
	srv := func(agent string) *rpc.Server {
		s := rpc.NewServer(auth{agent}, "x", store.LatestVersion(), "local", func(string, ...any) {})
		s.DB = d
		Register(s, home, f)
		taskresult.Register(s, home, d)
		return s
	}
	done = func(agent, rid string, task int, params string) map[string]any {
		return call(t, srv(agent), fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":"task.done","params":{"task":%d,"request_id":%q%s}}`, task, rid, params))[0]
	}
	ui = func(task int) {
		if err := taskresult.MarkUI(d, int64(task)); err != nil {
			t.Fatal(err)
		}
	}
	resultOf = func(task int) taskresult.Result {
		r, err := taskresult.Get(d, int64(task))
		if err != nil {
			t.Fatal(err)
		}
		return r
	}
	shotCall = func(agent string, task, n int) map[string]any {
		return call(t, srv(agent), fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":"task.shot","params":{"task":%d,"n":%d}}`, task, n))[0]
	}
	return
}

var (
	resultOf func(task int) taskresult.Result
	shotCall func(agent string, task, n int) map[string]any
)

func TestDoneKeepsTheCommitItWasDoneAt(t *testing.T) {
	_, repo, onMain, offMain, done, _ := newResultRig(t)
	ok := func(r map[string]any) {
		t.Helper()
		if r["result"] == nil {
			t.Fatalf("refused: %v", r)
		}
	}
	// a commit that is on no branch main reaches is kept as it is, with its place said
	ok(done("w", "r1", 1, fmt.Sprintf(`,"evidence":"built","commit":%q,"repo":%q`, offMain[:10], repo)))
	if got := resultOf(1); got.Commit != offMain || got.OnMain {
		t.Fatalf("%+v", got)
	}
	// a sha as evidence is a commit on main
	ok(done("w", "r2", 5, fmt.Sprintf(`,"evidence":%q,"repo":%q`, onMain, repo)))
	if got := resultOf(5); got.Commit != onMain || !got.OnMain {
		t.Fatalf("%+v", got)
	}
}

func TestDoneRefusesACommitThatIsNotOneAndAShaThatNeverLanded(t *testing.T) {
	_, repo, _, offMain, done, _ := newResultRig(t)
	if got := errText(done("w", "c1", 1, fmt.Sprintf(`,"evidence":"x","commit":"deadbeef","repo":%q`, repo))); !strings.Contains(got, "deadbeef is not a commit") {
		t.Fatalf("%q", got)
	}
	if got := errText(done("w", "c2", 1, `,"evidence":"x","commit":"deadbeef"`)); got != "a sha is checked against a repo: name one" {
		t.Fatalf("%q", got)
	}
	// evidence that is a sha must be on main
	if got := errText(done("w", "c4", 5, fmt.Sprintf(`,"evidence":%q,"repo":%q`, offMain, repo))); got != offMain+" is not an ancestor of main" {
		t.Fatalf("%q", got)
	}
	// a task that expects a sha does not take words from its owner
	if got := errText(done("w", "c5", 5, `,"evidence":"it is fine"`)); !strings.Contains(got, "expects a sha") {
		t.Fatalf("%q", got)
	}
	if resultOf(1).Commit != "" || resultOf(5).Commit != "" {
		t.Fatal("a refusal kept a commit")
	}
}

func TestAUITaskIsDoneWithOrWithoutAScreenshot(t *testing.T) {
	home, _, _, _, done, ui := newResultRig(t)
	ui(2)
	ui(3)
	ui(4)
	ui(6)
	ui(1)
	if r := done("w", "u1", 3, `,"evidence":"built"`); r["result"] == nil || !resultOf(3).Empty() {
		t.Fatalf("a ui task was refused without a shot: %v", r)
	}
	shot := pngFile(t, filepath.Join(home, "agents", "w"), "s.png", 1000)
	if r := done("w", "u2", 2, fmt.Sprintf(`,"evidence":"built","shots":[%q]`, shot)); r["result"] == nil {
		t.Fatalf("%v", r)
	}
	if got := resultOf(2); len(got.Shots) != 1 || got.Shots[0].N != 1 || got.Shots[0].Bytes != 1008 {
		t.Fatalf("%+v", got)
	}
	kept := filepath.Join(home, "shots", "T2", "1.png")
	st, err := os.Stat(kept)
	if err != nil || st.Mode().Perm() != 0o600 || st.Size() != 1008 {
		t.Fatalf("%v %v", st, err)
	}
	// what is not a screenshot is refused, and the task stays open
	txt := filepath.Join(t.TempDir(), "x.png")
	os.WriteFile(txt, []byte("not a png"), 0o644)
	for name, bad := range map[string]string{"not a png": txt, "relative": "s.png", "missing": "/no/such/shot.png"} {
		if r := done("w", "u-"+name, 4, fmt.Sprintf(`,"evidence":"built","shots":[%q]`, bad)); r["error"] == nil {
			t.Errorf("%s was taken: %v", name, r)
		}
	}
	// the founder closes it as they like
	if r := done("", "u4", 6, `,"evidence":"seen"`); r["result"] == nil {
		t.Fatalf("%v", r)
	}
}

func TestADoneTasksScreenshotIsServedByTaskAndNumber(t *testing.T) {
	home, _, _, _, done, ui := newResultRig(t)
	ui(2)
	shot := pngFile(t, filepath.Join(home, "agents", "w"), "s.png", 5000)
	want, _ := os.ReadFile(shot)
	done("w", "s1", 2, fmt.Sprintf(`,"evidence":"built","shots":[%q]`, shot))
	r, _ := shotCall("", 2, 1)["result"].(map[string]any)
	if r == nil || r["mime"] != "image/png" || r["done"] != true || r["offset"] != float64(0) || r["bytes"] != float64(len(want)) || r["data"] != base64.StdEncoding.EncodeToString(want) {
		t.Fatalf("%v", r)
	}
	for _, c := range [][2]int{{2, 2}, {1, 1}, {99, 1}} {
		e, _ := shotCall("", c[0], c[1])["error"].(map[string]any)
		if e == nil || e["data"].(map[string]any)["code"] != "not_found" {
			t.Errorf("T%d shot %d: %v", c[0], c[1], e)
		}
	}
}

func TestAnAgentsScreenshotsAreReadOnlyFromItsOwnPlaces(t *testing.T) {
	home, _, _, _, done, ui := newResultRig(t)
	ui(2)
	elsewhere := pngFile(t, t.TempDir(), "other.png", 100) // any PNG on the host: not the agent's to hand over
	if got := errText(done("w", "p1", 2, fmt.Sprintf(`,"evidence":"built","shots":[%q]`, elsewhere))); !strings.Contains(got, "outside the places a screenshot may be taken from") {
		t.Fatalf("%q", got)
	}
	link := filepath.Join(home, "agents", "w", "link.png")
	if err := os.Symlink(elsewhere, link); err != nil {
		t.Fatal(err)
	}
	if got := errText(done("w", "p2", 2, fmt.Sprintf(`,"evidence":"built","shots":[%q]`, link))); !strings.Contains(got, "outside the places") {
		t.Fatalf("a link out of the folder: %q", got)
	}
	// the founder names any file
	if r := done("", "p3", 3, fmt.Sprintf(`,"evidence":"seen","shots":[%q]`, elsewhere)); r["result"] == nil {
		t.Fatalf("%v", r)
	}
}
