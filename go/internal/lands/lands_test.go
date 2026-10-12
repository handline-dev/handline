package lands

import (
	"bufio"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/session"
	"github.com/handline/handline/go/internal/store"
)

// teamName is the team the merger belongs to; the other agents of the tests are its members.
var teamName = strings.TrimSuffix(Merger, ".merger")

const core = "x.core"

type fakeTokens struct {
	sessions map[string]int // agent -> the pid of its session
	keys     map[string]string
}

func (f fakeTokens) Verify(agent string, pid int) bool {
	p, ok := f.sessions[agent]
	return ok && p == pid
}
func (f fakeTokens) KeyOK(agent, key string) bool { k, ok := f.keys[agent]; return ok && k == key }

func git(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", append([]string{"-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"}, args...)...)
	cmd.Dir = dir
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
	return strings.TrimSpace(string(out))
}

// rig is a repository with main checked out and a branch one commit ahead, a database, and a service over them.
type rig struct {
	s          *Service
	repo       string
	sha, tree  string
	mu         sync.Mutex
	told       []string
	merger     string
	auditor    string
	mergerKey  string
	auditorKey string
}

func newRig(t *testing.T) *rig {
	t.Helper()
	repo := t.TempDir()
	git(t, repo, "init", "-q", "-b", "main")
	os.WriteFile(filepath.Join(repo, "a.txt"), []byte("a\n"), 0o644)
	git(t, repo, "add", ".")
	git(t, repo, "commit", "-q", "-m", "first")
	git(t, repo, "checkout", "-q", "-b", "feature")
	os.WriteFile(filepath.Join(repo, "b.txt"), []byte("b\n"), 0o644)
	git(t, repo, "add", ".")
	git(t, repo, "commit", "-q", "-m", "second")
	r := &rig{repo: repo, sha: git(t, repo, "rev-parse", "HEAD"), tree: git(t, repo, "rev-parse", "HEAD^{tree}"),
		merger: Merger, auditor: teamName + ".auditor"}
	git(t, repo, "checkout", "-q", "main")
	d, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	now := time.Unix(1000, 0)
	r.s = &Service{DB: d, Repo: repo, Now: func() time.Time { return now },
		Tokens: fakeTokens{sessions: map[string]int{r.merger: 10, r.auditor: 20, core: 30},
			keys: map[string]string{r.merger: "mk", r.auditor: "ak", core: "ck"}},
		Tell: func(to, body string) {
			r.mu.Lock()
			defer r.mu.Unlock()
			r.told = append(r.told, to+": "+body)
		}}
	return r
}

func (r *rig) vouch(verb, sha, agent string) map[string]any {
	key := map[string]string{r.merger: "mk", r.auditor: "ak", core: "ck"}[agent]
	return r.s.Legacy(map[string]any{"verb": "land_authorize", "agent": agent, "land_verb": verb, "sha": sha, "key": key}, 0)
}

func TestAGrantIsSpentOnceBindsTheShaAndRunsOut(t *testing.T) {
	now := time.Unix(1000, 0)
	g := &Grants{Now: func() time.Time { return now }}
	g.Grant("a.b", "land", "abc")
	if g.Take("a.b", "land_pass", "abc") || g.Take("a.b", "land", "abcd") || g.Take("c.d", "land", "abc") {
		t.Fatal("a vouch binds the agent, the verb and the sha as typed")
	}
	if !g.Take("a.b", "land", "abc") || g.Take("a.b", "land", "abc") {
		t.Fatal("a vouch is used once")
	}
	g.Grant("a.b", "land", "abc")
	now = now.Add(GrantTTL)
	if g.Take("a.b", "land", "abc") {
		t.Fatal("a vouch lives for a minute")
	}
	g.Grant("a.b", "land", "abc")
	now = now.Add(GrantTTL - time.Second)
	if !g.Take("a.b", "land", "abc") {
		t.Fatal("a vouch is good until the minute is out")
	}
}

func TestAuthorizeNeedsTheSessionKeyAndALandVerb(t *testing.T) {
	r := newRig(t)
	for name, p := range map[string]map[string]any{
		"no key":         {"verb": "land_authorize", "agent": r.merger, "land_verb": "land", "sha": "x"},
		"another's key":  {"verb": "land_authorize", "agent": r.merger, "land_verb": "land", "sha": "x", "key": "ak"},
		"another verb":   {"verb": "land_authorize", "agent": r.merger, "land_verb": "wake", "sha": "x", "key": "mk"},
		"no such agent":  {"verb": "land_authorize", "agent": "nobody", "land_verb": "land", "sha": "x", "key": "mk"},
		"agent not text": {"verb": "land_authorize", "agent": 5, "land_verb": "land", "sha": "x", "key": "mk"},
	} {
		got := r.s.Legacy(p, 10)
		if got["ok"] != false || got["said"] != "not authorized" {
			t.Errorf("%s: %v", name, got)
		}
	}
	if got := r.vouch("land", "x", r.merger); got["ok"] != true || got["said"] != "vouched" {
		t.Fatalf("%v", got)
	}
}

func TestALandVerbNeedsTheSessionAndItsOwnVouch(t *testing.T) {
	r := newRig(t)
	pass := map[string]any{"verb": "land_pass", "agent": r.auditor, "sha": r.sha}
	if got := r.s.Legacy(pass, 99); got["ok"] != false || !strings.Contains(got["said"].(string), "is not this request's session") {
		t.Fatalf("a process that is not the session: %v", got)
	}
	if got := r.s.Legacy(pass, 20); got["ok"] != false || !strings.Contains(got["said"].(string), "accepted only from the session's own `hl land` command") {
		t.Fatalf("no vouch: %v", got)
	}
	r.vouch("land_pass", "another sha", r.auditor)
	if got := r.s.Legacy(pass, 20); got["ok"] != false || !strings.Contains(got["said"].(string), "accepted only") {
		t.Fatalf("a vouch for another sha: %v", got)
	}
	r.vouch("land_pass", r.sha, r.auditor)
	if got := r.s.Legacy(pass, 20); got["ok"] != true || !strings.HasPrefix(got["said"].(string), "PASS recorded for "+r.sha[:8]) {
		t.Fatalf("%v", got)
	}
	if got := r.s.Legacy(pass, 20); got["ok"] != false {
		t.Fatalf("a vouch is used once: %v", got)
	}
}

func TestOnlyAnAuditorPassesAndOnlyTheMergerLands(t *testing.T) {
	r := newRig(t)
	r.vouch("land_pass", r.sha, core)
	if got := r.s.Legacy(map[string]any{"verb": "land_pass", "agent": core, "sha": r.sha}, 30); got["ok"] != false ||
		!strings.Contains(got["said"].(string), "may not record a PASS") {
		t.Fatalf("%v", got)
	}
	r.vouch("land", r.sha, core)
	got := r.s.Legacy(map[string]any{"verb": "land", "agent": core, "sha": r.sha, "message": "m", "tree": r.tree}, 30)
	if got["ok"] != false || !strings.Contains(got["said"].(string), "may not land a branch; the merger does") {
		t.Fatalf("%v", got)
	}
	for _, name := range []string{teamName + ".auditor", "x.auditor2", "a.b.auditor"} {
		if !IsAuditor(name) {
			t.Errorf("%s is an auditor", name)
		}
	}
	for _, name := range []string{"auditor", Merger, teamName + ".auditorx", teamName + ".auditor.go", ""} {
		if IsAuditor(name) {
			t.Errorf("%s is not", name)
		}
	}
}

func TestALandNeedsAPassItsTreeAndLandsOnce(t *testing.T) {
	r := newRig(t)
	land := func(extra map[string]any) map[string]any {
		r.vouch("land", r.sha, r.merger)
		p := map[string]any{"verb": "land", "agent": r.merger, "sha": r.sha, "message": "Merge the feature", "tree": r.tree}
		for k, v := range extra {
			p[k] = v
		}
		return r.s.Legacy(p, 10)
	}
	if got := land(nil); got["ok"] != false || !strings.Contains(got["said"].(string), "no auditor PASS is recorded") {
		t.Fatalf("%v", got)
	}
	r.vouch("land_pass", r.sha, r.auditor)
	if got := r.s.Legacy(map[string]any{"verb": "land_pass", "agent": r.auditor, "sha": r.sha}, 20); got["ok"] != true {
		t.Fatalf("%v", got)
	}
	if got := land(map[string]any{"tree": " "}); got["ok"] != false || !strings.Contains(got["said"].(string), "needs the merge message and the tree") {
		t.Fatalf("%v", got)
	}
	if got := land(map[string]any{"fix": map[string]any{"CLAUDE.md": "x"}}); got["ok"] != false || !strings.Contains(got["said"].(string), "needs a PASS") {
		t.Fatalf("an instruction file is never housekeeping: %v", got)
	}
	got := land(nil)
	if got["ok"] != true || !strings.Contains(got["said"].(string), "queued") {
		t.Fatalf("%v", got)
	}
	if got := land(nil); got["ok"] != false || !strings.Contains(got["said"].(string), "is already queued (land #1); a sha lands once") {
		t.Fatalf("%v", got)
	}
	if err := r.s.Sweep(); err != nil {
		t.Fatal(err)
	}
	waitFor(t, func() bool { return len(r.said()) == 1 })
	if !strings.Contains(r.said()[0], Merger+": land #1: "+r.sha[:8]+" is on main as ") {
		t.Fatalf("%v", r.said())
	}
	if git(t, r.repo, "show", "main:b.txt") != "b" || git(t, r.repo, "rev-parse", "main^{tree}") != r.tree {
		t.Fatal("main holds the branch")
	}
	if got := land(nil); got["ok"] != false || !strings.Contains(got["said"].(string), "is already landed as ") {
		t.Fatalf("%v", got)
	}
}

func TestALandWhoseTreeIsNotTheOneTestedFailsAndSaysSo(t *testing.T) {
	r := newRig(t)
	r.vouch("land_pass", r.sha, r.auditor)
	r.s.Legacy(map[string]any{"verb": "land_pass", "agent": r.auditor, "sha": r.sha}, 20)
	r.vouch("land", r.sha, r.merger)
	got := r.s.Legacy(map[string]any{"verb": "land", "agent": r.merger, "sha": r.sha, "message": "m", "tree": "0000"}, 10)
	if got["ok"] != true {
		t.Fatalf("%v", got)
	}
	before := git(t, r.repo, "rev-parse", "main")
	r.s.Sweep()
	waitFor(t, func() bool { return len(r.said()) == 1 })
	if !strings.Contains(r.said()[0], "did not land") || !strings.Contains(r.said()[0], "not the tree it tested") ||
		git(t, r.repo, "rev-parse", "main") != before {
		t.Fatalf("%v", r.said())
	}
}

func TestAGoHookVouchIsAcceptedByTheGoDaemon(t *testing.T) {
	r := newRig(t)
	v := session.LandVoucher{KeyHeld: func(agent string) string { return map[string]string{r.auditor: "ak"}[agent] },
		Send: func(req map[string]any, _ time.Duration) map[string]any { return r.s.Legacy(req, 0) }}
	if !v.Authorize(r.auditor, "Bash", map[string]any{"command": "hl land --pass " + r.sha}) {
		t.Fatal("the daemon took the vouch of the Go hook")
	}
	if v.Authorize(core, "Bash", map[string]any{"command": "hl land --pass " + r.sha}) {
		t.Fatal("an agent with no key held vouches for nothing")
	}
	if v.Authorize(r.auditor, "Bash", map[string]any{"command": "hl land --pass " + r.sha + "; rm -rf x"}) {
		t.Fatal("a command with more in it vouches for nothing")
	}
	if got := r.s.Legacy(map[string]any{"verb": "land_pass", "agent": r.auditor, "sha": r.sha}, 20); got["ok"] != true {
		t.Fatalf("the vouch lets the verb in: %v", got)
	}
}

func TestOtherVerbsAreNotServedHere(t *testing.T) {
	r := newRig(t)
	if got := r.s.Legacy(map[string]any{"verb": "wake", "agent": "x"}, 1); got != nil {
		t.Fatalf("%v", got)
	}
}

func TestHousekeepingPathsAreRefusedByTheRulesTheMergerSees(t *testing.T) {
	globs := ReadGlobs("# a comment\ndocs/**/*.md  # trailing\n*.txt\n\n")
	for path, wantRefused := range map[string]bool{
		"docs/a.md": false, "docs/x/y/z.md": false, "notes.txt": false, "docs/a.go": true, "sub/notes.txt": true,
		"CLAUDE.md": true, "docs/Claude.md": true, "docs/.git/x.md": true, "skill/x.txt": true, "../x.txt": true, "/x.txt": true,
		"a//b.txt": true, "": true, "docs\\a.md": true,
	} {
		if got := Refusal(path, globs) != ""; got != wantRefused {
			t.Errorf("%q: refused %v, want %v", path, got, wantRefused)
		}
	}
	work := t.TempDir()
	outside := t.TempDir()
	os.Symlink(outside, filepath.Join(work, "link"))
	if why := WriteFiles(work, map[string]string{"link/x.txt": "x"}); !strings.Contains(why, "symlink") {
		t.Fatalf("%q", why)
	}
	if _, err := os.Stat(filepath.Join(outside, "x.txt")); err == nil {
		t.Fatal("written through a symlink")
	}
	if why := WriteFiles(work, map[string]string{"d/x.txt": "x"}); why != "" {
		t.Fatal(why)
	}
}

func (r *rig) said() []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]string(nil), r.told...)
}

func waitFor(t *testing.T, ok func() bool) {
	t.Helper()
	for i := 0; i < 600; i++ {
		if ok() {
			return
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatal("timed out")
}

type agentAuth struct{ agent string }

func (a agentAuth) AgentOf(int) string     { return a.agent }
func (agentAuth) FounderKeyOK(string) bool { return false }

func TestTheJSONRPCMethodsTakeTheSameRulesAsTheLegacyVerbs(t *testing.T) {
	r := newRig(t)
	srv := rpc.NewServer(agentAuth{r.auditor}, "abc1234", 7, "local", func(string, ...any) {})
	srv.DB = r.s.DB
	Register(srv, r.s)
	cl, sv := net.Pipe()
	go func() { srv.Serve(rpc.NewLineTransport(sv), 20); sv.Close() }()
	defer cl.Close()
	rd := bufio.NewReader(cl)
	call := func(id int, method, params string) map[string]any {
		cl.SetDeadline(time.Now().Add(10 * time.Second))
		fmt.Fprintf(cl, `{"jsonrpc":"2.0","id":%d,"method":%q,"params":%s}`+"\n", id, method, params)
		line, err := rd.ReadBytes('\n')
		if err != nil {
			t.Fatal(err)
		}
		var m map[string]any
		json.Unmarshal(line, &m)
		return m
	}
	gate := func(m map[string]any) string {
		e, _ := m["error"].(map[string]any)
		d, _ := e["data"].(map[string]any)
		g, _ := d["gate"].(string)
		return g
	}
	call(1, "hello", `{"client":"t","protocol":1}`)
	pass := `{"sha":"` + r.sha + `","request_id":"%s"}`
	if g := gate(call(2, "land.pass", fmt.Sprintf(pass, "p1"))); g != "session_token" {
		t.Fatalf("no vouch: gate %q", g)
	}
	if g := gate(call(3, "land.authorize", `{"agent":"`+Merger+`","land_verb":"land_pass","sha":"`+r.sha+`","key":"ak","request_id":"a0"}`)); g != "not_self" {
		t.Fatalf("another agent's name: gate %q", g)
	}
	if g := gate(call(4, "land.authorize", `{"agent":"`+r.auditor+`","land_verb":"land_pass","sha":"`+r.sha+`","key":"wrong","request_id":"a1"}`)); g != "session_token" {
		t.Fatalf("a wrong key: gate %q", g)
	}
	if m := call(5, "land.authorize", `{"agent":"`+r.auditor+`","land_verb":"land_pass","sha":"`+r.sha+`","key":"ak","request_id":"a2"}`); m["error"] != nil {
		t.Fatalf("%v", m)
	}
	m := call(6, "land.pass", fmt.Sprintf(pass, "p2"))
	res, _ := m["result"].(map[string]any)
	if res["accepted"] != true || !strings.HasPrefix(res["said"].(string), "PASS recorded for ") {
		t.Fatalf("%v", m)
	}
	if g := gate(call(7, "land.pass", fmt.Sprintf(pass, "p3"))); g != "session_token" {
		t.Fatalf("the vouch is spent: gate %q", g)
	}
}

func TestNoTransactionIsOpenWhileAMessageIsCheckedOrGitIsAsked(t *testing.T) {
	r := newRig(t)
	open := filepath.Join(t.TempDir(), "tx-open")
	tools := filepath.Join(r.repo, "tools")
	os.MkdirAll(tools, 0o755)
	// main's localrefs.py, as the daemon runs it: it fails when the transaction's marker is there
	script := "import os, sys\nsys.exit('a transaction is open' if os.path.exists(" + strconv.Quote(open) + ") else 0)\n"
	os.WriteFile(filepath.Join(tools, "localrefs.py"), []byte(script), 0o644)
	r.vouch("land_pass", r.sha, r.auditor)
	r.s.Legacy(map[string]any{"verb": "land_pass", "agent": r.auditor, "sha": r.sha}, 20)
	tx := func(fn func(db.Q) error) error {
		os.WriteFile(open, nil, 0o644)
		defer os.Remove(open)
		return r.s.inTx(fn)
	}
	ok, said := r.s.run("land", map[string]any{"sha": r.sha, "message": "Merge the feature", "tree": r.tree}, r.merger, tx)
	if !ok || !strings.Contains(said, "queued") {
		t.Fatalf("%v %s", ok, said)
	}
}

func (agentAuth) SessionsReadable() error { return nil }
