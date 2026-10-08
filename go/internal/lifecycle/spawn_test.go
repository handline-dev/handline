package lifecycle

import (
	"database/sql"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
)

func gitIn(t *testing.T, dir string, args ...string) {
	t.Helper()
	cmd := exec.Command("git", append([]string{"-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"}, args...)...)
	cmd.Dir = dir
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
}

func repoWithOrigin(t *testing.T) (repo, origin string) {
	t.Helper()
	origin = t.TempDir()
	gitIn(t, origin, "init", "-q", "-b", "main", "--bare")
	repo = t.TempDir()
	gitIn(t, repo, "init", "-q", "-b", "main")
	gitIn(t, repo, "remote", "add", "origin", origin)
	gitIn(t, repo, "commit", "-q", "--allow-empty", "-m", "one")
	gitIn(t, repo, "push", "-q", "origin", "main")
	return
}

func TestPickBaseBranchesFromTheSideThatLeadsAndRefusesDivergence(t *testing.T) {
	repo, _ := repoWithOrigin(t)
	if ref, why, _ := PickBase(repo, "main"); ref != "main" || why != "main and origin/main agree" {
		t.Fatalf("%q %q", ref, why)
	}
	gitIn(t, repo, "commit", "-q", "--allow-empty", "-m", "two")
	if ref, why, _ := PickBase(repo, "main"); ref != "main" || why != "local main leads by 1" {
		t.Fatalf("%q %q", ref, why)
	}
	gitIn(t, repo, "push", "-q", "origin", "main")
	gitIn(t, repo, "reset", "-q", "--hard", "HEAD~1")
	if ref, why, _ := PickBase(repo, "main"); ref != "origin/main" || why != "origin/main leads by 1" {
		t.Fatalf("%q %q", ref, why)
	}
	gitIn(t, repo, "commit", "-q", "--allow-empty", "-m", "other")
	if ref, why, _ := PickBase(repo, "main"); ref != "" || !strings.Contains(why, "have diverged (1 local, 1 remote)") {
		t.Fatalf("%q %q", ref, why)
	}
}

func TestPickBaseWithNoBaseUsesTheCheckedOutBranch(t *testing.T) {
	repo := t.TempDir()
	gitIn(t, repo, "init", "-q", "-b", "trunk")
	if ref, _, _ := PickBase(repo, "main"); ref != "" {
		t.Fatalf("a repo with no commit has nothing to branch from: %q", ref)
	}
	gitIn(t, repo, "commit", "-q", "--allow-empty", "-m", "one")
	if ref, why, _ := PickBase(repo, "main"); ref != "trunk" || !strings.Contains(why, "using the checked-out trunk") {
		t.Fatalf("%q %q", ref, why)
	}
}

func TestAWorktreeIsFoundThroughAnOrcaSelectorOrAPlainPath(t *testing.T) {
	dir := t.TempDir()
	if WorktreePath("repo-id::"+dir) != dir || WorktreePath(dir) != dir || WorktreePath(filepath.Join(dir, "gone")) != "" || WorktreePath("") != "" {
		t.Fatal("selector or path not resolved")
	}
	row := &db.AgentState{Worktree: ns(dir), ExtraWorktrees: ns(`["` + dir + `", "/nowhere", 3]`)}
	if got := AllPaths(row); len(got) != 2 || got[0] != dir || got[1] != dir {
		t.Fatalf("%v", got)
	}
	if ExtraPaths(&db.AgentState{ExtraWorktrees: ns("not json")}) != nil {
		t.Fatal("garbage should list nothing")
	}
}

type fakeHost struct {
	Host
	opened []string
	handle string
}

func (*fakeHost) Available() bool { return true }
func (*fakeHost) HasRuns() bool   { return false }
func (h *fakeHost) OpenTerminal(cmd, cwd, title string) (string, string) {
	h.opened = append(h.opened, cmd+"|"+cwd+"|"+title)
	return h.handle, ""
}

type upDelivery struct {
	Delivery
	up bool
}

func (d upDelivery) WaitForSession(string, int, string) bool { return d.up }

func TestStartInOpensTheTerminalInTheKeptWorktreeAndChecksASessionCameUp(t *testing.T) {
	wt := t.TempDir()
	q := openDB(t)
	h := &fakeHost{handle: "t9"}
	s := &Spawns{Q: q, Host: h, Launch: fakeLaunch{}, Delivery: upDelivery{up: true}, Sleep: func(time.Duration) {}}
	ok, said, _ := s.StartIn("a", wt, "hl launch a --why 'wake'")
	if !ok || !strings.Contains(said, "a: started in "+wt+" (terminal t9)") {
		t.Fatalf("%v %q", ok, said)
	}
	if row, _ := db.GetAgentState(q, "a"); row.Terminal.String != "t9" || row.Worktree.String != wt {
		t.Fatalf("%+v", row)
	}
	s.Delivery = upDelivery{up: false}
	if ok, said, _ := s.StartIn("a", wt, "x"); ok || !strings.Contains(said, "no session is running in it after 20s") {
		t.Fatalf("%v %q", ok, said)
	}
	if ok, said, _ := s.StartIn("a", filepath.Join(wt, "gone"), "x"); ok || !strings.Contains(said, "its worktree") {
		t.Fatalf("%v %q", ok, said)
	}
}

func TestPromptLandedWaitsForTheAgentsOwnHook(t *testing.T) {
	q := openDB(t)
	now := time.Unix(0, 0)
	s := &Spawns{Q: q, Now: func() time.Time { return now }, Sleep: func(d time.Duration) { now = now.Add(d) }}
	if ok, _ := s.PromptLanded("a", 0, 10*time.Second); ok {
		t.Fatal("landed with no event")
	}
	since, _ := db.MaxEventID(q)
	db.AddEvent(q, "a", "UserPromptSubmit")
	if ok, _ := s.PromptLanded("a", since, 10*time.Second); !ok {
		t.Fatal("the hook was not believed")
	}
}

func ns(s string) sql.NullString { return sql.NullString{String: s, Valid: s != ""} }

type runHost struct {
	fakeHost
	wtMade     string
	missing    string
	current    string
	exists     bool
	created    string
	used, runs []string
}

func (h *runHost) Available() bool { return h.missing == "" }
func (h *runHost) Missing() string { return h.missing }
func (h *runHost) HasRuns() bool   { return true }
func (h *runHost) CreateWorktree(_, name, base string) (string, string) {
	if h.wtMade == "" {
		return "", "worktree create failed"
	}
	return h.wtMade, ""
}
func (h *runHost) Manual(wt, repo, name, base, cmd string) string { return "manual: " + cmd }
func (h *runHost) RunCurrent() string                             { return h.current }
func (h *runHost) RunExists(string) bool                          { return h.exists }
func (h *runHost) RunUse(run string)                              { h.used = append(h.used, run) }
func (h *runHost) RunCreate(string) string                        { h.runs = append(h.runs, h.created); return h.created }
func (h *runHost) TaskCreate(_, title, _, run, parent string) string {
	return "task-" + title
}
func (h *runHost) Dispatch(task, handle, run string) string { return task + "@" + handle }

func spawnRig(t *testing.T) (*Spawns, *runHost, string) {
	t.Helper()
	repo, _ := repoWithOrigin(t)
	h := &runHost{fakeHost: fakeHost{handle: "t7"}, wtMade: t.TempDir(), created: "run-1"}
	s := &Spawns{Q: openDB(t), Host: h, Launch: fakeLaunch{}, Delivery: upDelivery{up: true}, Sleep: func(time.Duration) {}}
	return s, h, repo
}

func TestSpawnBranchesAWorktreeFromTheLeadingSideAndOpensItsTerminal(t *testing.T) {
	s, h, repo := spawnRig(t)
	ok, said, _ := s.Spawn("a", repo)
	if !ok || !strings.Contains(said, "a: worktree "+h.wtMade) || !strings.Contains(said, "branched from main (main and origin/main agree)") || !strings.Contains(said, "terminal t7") {
		t.Fatalf("%v %q", ok, said)
	}
	row, _ := db.GetAgentState(s.Q, "a")
	if row.Terminal.String != "t7" || row.Worktree.String != h.wtMade {
		t.Fatalf("%+v", row)
	}
	if len(h.opened) != 1 || !strings.HasPrefix(h.opened[0], "hl launch a") {
		t.Fatalf("%v", h.opened)
	}
	// Its own worktree is kept: no second one is made.
	h.wtMade = ""
	if ok, said, _ := s.Spawn("a", repo); !ok || !strings.Contains(said, "kept (its own worktree, kept)") {
		t.Fatalf("%v %q", ok, said)
	}
}

func TestSpawnWithSaysWhyItCannotAndWhatItWouldDo(t *testing.T) {
	s, h, repo := spawnRig(t)
	h.missing = "orca is not running"
	if ok, said, err := s.SpawnWith("a", repo, SpawnOpts{}); ok || err != nil || said != "orca is not running" {
		t.Fatalf("%v %q %v", ok, said, err)
	}
	h.missing = ""
	if ok, said, _ := s.Spawn("a", t.TempDir()); ok || said == "" {
		t.Fatalf("a directory that is no repo has nothing to branch from: %v %q", ok, said)
	}
	h.wtMade = ""
	if ok, said, _ := s.Spawn("a", repo); ok || said != "worktree create failed" {
		t.Fatalf("%v %q", ok, said)
	}
	ok, said, err := s.SpawnWith("a", repo, SpawnOpts{DryRun: true})
	if err != nil || !ok || !strings.HasPrefix(said, "would branch from main (main and origin/main agree)") || len(h.opened) != 0 {
		t.Fatalf("%v %q %v %v", ok, said, err, h.opened)
	}
}

func TestEnsureRunRemembersTheRunAndPrefersTheOneTheTerminalIsBoundTo(t *testing.T) {
	s, h, _ := spawnRig(t)
	run, err := s.EnsureRun()
	if err != nil || run != "run-1" || len(h.runs) != 1 {
		t.Fatalf("%q %v", run, err)
	}
	h.exists = true
	if run, _ := s.EnsureRun(); run != "run-1" || len(h.runs) != 1 || len(h.used) != 1 {
		t.Fatalf("a remembered run is reused, not recreated: %q %v %v", run, h.runs, h.used)
	}
	h.current = "bound"
	if run, _ := s.EnsureRun(); run != "bound" {
		t.Fatalf("%q", run)
	}
	if v, _, _ := db.Meta(s.Q, RunKey); v != "bound" {
		t.Fatalf("%q", v)
	}
	if task, err := s.RegisterWorker("a", "t1"); err != nil || task != "task-a@t1" {
		t.Fatalf("%q %v", task, err)
	}
}

func TestGitThatCannotRunIsAnErrorNotAMissingBranch(t *testing.T) {
	gone := filepath.Join(t.TempDir(), "gone")
	if ref, why, err := PickBase(gone, "main"); err == nil || ref != "" || strings.Contains(why, "make a first commit") {
		t.Fatalf("%q %q %v", ref, why, err)
	}
	s, _, _ := spawnRig(t)
	if ok, said, err := s.SpawnWith("a", gone, SpawnOpts{}); ok || err == nil {
		t.Fatalf("%v %q %v", ok, said, err)
	}
}

func TestGitThatTimesOutIsAnError(t *testing.T) {
	saved := gitTimeout
	gitTimeout = time.Nanosecond
	defer func() { gitTimeout = saved }()
	if _, _, _, err := git(t.TempDir(), "status"); err == nil || !strings.Contains(err.Error(), "timed out") {
		t.Fatalf("a killed git is an error: %v", err)
	}
}
