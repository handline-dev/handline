package devinstance

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/store"
)

// rig is a scratch user home with a live home whose database has a row the snapshot must carry, a fake launchd and a
// fake build: nothing reaches the real launchd or the real homes.
type rig struct {
	*Env
	loaded map[string]bool
	calls  []string
	sha    string
}

func newRig(t *testing.T) *rig {
	t.Helper()
	user := t.TempDir()
	live := filepath.Join(user, ".handline")
	db, err := store.Open(paths.DBFile(live))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })
	wt := filepath.Join(user, "wt")
	if _, err := db.Exec("INSERT INTO agent_state(agent, state, updated, worktree) VALUES('handline.daemon', 'idle', 1, ?)", wt); err != nil {
		t.Fatal(err)
	}
	os.WriteFile(filepath.Join(live, "team.yaml"), []byte("agents: {}\n"), 0o644)
	os.MkdirAll(filepath.Join(live, "agents", "handline.daemon"), 0o755)
	os.WriteFile(filepath.Join(live, "agents", "handline.daemon", "agent.yaml"), []byte("name: handline.daemon\n"), 0o644)
	r := &rig{loaded: map[string]bool{}, sha: "abc1234"}
	r.Env = &Env{UserHome: user, LiveHome: live, UID: 501, Path: "/usr/bin",
		Launchctl: func(args ...string) (string, error) {
			r.calls = append(r.calls, "launchctl "+strings.Join(args, " "))
			target := args[len(args)-1]
			label := filepath.Base(target)
			switch args[0] {
			case "print":
				if !r.loaded[label] {
					return "could not find service", errors.New("exit status 113")
				}
			case "bootstrap":
				label = strings.TrimSuffix(label, ".plist")
				if _, err := os.Stat(target); err != nil {
					return "", err
				}
				r.loaded[label] = true
			case "bootout":
				delete(r.loaded, label)
			}
			return "", nil
		},
		Run: func(dir, name string, args ...string) (string, error) {
			r.calls = append(r.calls, dir+": "+filepath.Base(name)+" "+strings.Join(args, " "))
			switch {
			case name == "git" && args[1] == "--abbrev-ref":
				return "dev-branch\n", nil
			case name == "git":
				return r.sha + "\n", nil
			}
			return "", os.WriteFile(args[0], []byte("binary "+r.sha), 0o755)
		},
		PortFree: func(addr string) bool { return addr != "127.0.0.1:7999" },
	}
	return r
}

func (r *rig) count(prefix string) int {
	n := 0
	for _, c := range r.calls {
		if strings.HasPrefix(c, prefix) {
			n++
		}
	}
	return n
}

func TestOnSeedsBuildsAndStartsTheTeamsOwnInstance(t *testing.T) {
	r := newRig(t)
	liveBefore := snapshotDir(t, r.LiveHome)
	in, err := r.On("handline.daemon", OnOpts{})
	if err != nil {
		t.Fatal(err)
	}
	home := filepath.Join(r.UserHome, ".handline-dev", "handline.daemon")
	want := Instance{Team: "handline.daemon", Home: home, Worktree: filepath.Join(r.UserHome, "wt"), Branch: "dev-branch", Sha: "abc1234",
		Socket: filepath.Join(home, "daemon.sock"), Gateway: "127.0.0.1:7451", Labels: Labels("handline.daemon")}
	if got, _ := r.Read("handline.daemon"); got == nil || !same(*got, want) || !same(*in, want) {
		t.Fatalf("instance.json %+v, want %+v", got, want)
	}
	after := snapshotDir(t, r.LiveHome)
	for f, b := range liveBefore {
		if !strings.HasSuffix(f, "-shm") && !bytes.Equal(b, after[f]) { // -shm is the readers' shared index, which a reader updates
			t.Fatalf("%s was written", f)
		}
	}
	if len(after) != len(liveBefore) {
		t.Fatalf("the live home gained files: %d -> %d", len(liveBefore), len(after))
	}
	dev, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer dev.Close()
	var wt string
	if err := dev.QueryRow("SELECT worktree FROM agent_state WHERE agent = 'handline.daemon'").Scan(&wt); err != nil || wt != want.Worktree {
		t.Fatalf("the snapshot carries the live rows, even ones not checkpointed yet: %q %v", wt, err)
	}
	for _, f := range []string{"team.yaml", "agents/handline.daemon/agent.yaml", "bin/handlined"} {
		if _, err := os.Stat(filepath.Join(home, f)); err != nil {
			t.Errorf("%s: %v", f, err)
		}
	}
	daemon, _ := os.ReadFile(filepath.Join(r.UserHome, "Library", "LaunchAgents", "dev.handline.dev.handline.daemon.plist"))
	gateway, _ := os.ReadFile(filepath.Join(r.UserHome, "Library", "LaunchAgents", "dev.handline.dev.handline.daemon.gateway.plist"))
	bin := filepath.Join(home, "bin", "handlined")
	if !strings.Contains(string(daemon), "<string>"+bin+"</string><string>-home</string><string>"+home+"</string></array>") {
		t.Fatalf("the daemon runs with -home only, so it ticks no session:\n%s", daemon)
	}
	if !strings.Contains(string(gateway), "<string>-gateway</string><string>-home</string><string>"+home+"</string><string>-listen</string><string>127.0.0.1:7451</string>") {
		t.Fatalf("gateway plist:\n%s", gateway)
	}
	if !strings.Contains(string(daemon), "<key>HANDLINE_HOME</key><string>"+home+"</string>") {
		t.Fatalf("the job's environment names its own home:\n%s", daemon)
	}
	if !r.loaded["dev.handline.dev.handline.daemon"] || !r.loaded["dev.handline.dev.handline.daemon.gateway"] || len(r.loaded) != 2 {
		t.Fatalf("loaded %v", r.loaded)
	}
	if lines := r.Status(); !strings.Contains(strings.Join(lines, "\n"), "handline.daemon  on  dev-branch@abc1234  "+want.Socket+"  127.0.0.1:7451") ||
		!strings.HasPrefix(lines[1], "handline.mobile  off") {
		t.Fatalf("status:\n%s", strings.Join(lines, "\n"))
	}
}

func TestOnRefusesWhatWouldTouchTheLiveHomeOrAnotherInstance(t *testing.T) {
	r := newRig(t)
	if _, err := r.On("handline.web", OnOpts{}); err == nil || !strings.Contains(err.Error(), "one of handline.daemon, handline.mobile, handline.pc") {
		t.Fatalf("unknown team: %v", err)
	}
	if _, err := r.On("handline.pc", OnOpts{Listen: "127.0.0.1:7999"}); err == nil || !strings.Contains(err.Error(), "127.0.0.1:7999 is in use") {
		t.Fatalf("a port in use: %v", err)
	}
	if _, err := r.On("handline.pc", OnOpts{}); err == nil || !strings.Contains(err.Error(), "handline.pc has no worktree on record") {
		t.Fatalf("no worktree for the manager: %v", err)
	}
	live := r.LiveHome
	r.LiveHome = filepath.Join(r.UserHome, ".handline-dev")
	if _, err := r.On("handline.daemon", OnOpts{}); err == nil || !strings.Contains(err.Error(), "overlaps the live home") {
		t.Fatalf("a dev home inside the live one: %v", err)
	}
	os.MkdirAll(filepath.Join(r.UserHome, ".handline-dev"), 0o755)
	os.Symlink(live, filepath.Join(r.UserHome, ".handline-dev", "handline.daemon"))
	r.LiveHome = live
	if _, err := r.On("handline.daemon", OnOpts{}); err == nil || !strings.Contains(err.Error(), "overlaps the live home") {
		t.Fatalf("a dev home that resolves to the live one: %v", err)
	}
	if r.count("launchctl bootstrap") != 0 || r.count(filepath.Join(r.UserHome, "wt")) != 0 {
		t.Fatalf("a refusal starts and builds nothing: %v", r.calls)
	}
	os.Remove(filepath.Join(r.UserHome, ".handline-dev", "handline.daemon"))
	if _, err := r.On("handline.daemon", OnOpts{}); err != nil {
		t.Fatal(err)
	}
	if _, err := r.On("handline.daemon", OnOpts{}); err == nil || !strings.Contains(err.Error(), "already on") {
		t.Fatalf("on twice: %v", err)
	}
	if _, err := r.On("handline.pc", OnOpts{Worktree: filepath.Join(r.UserHome, "pc")}); err != nil {
		t.Fatalf("another team's instance starts beside it: %v", err)
	}
	if err := r.Off("handline.pc"); err != nil {
		t.Fatal(err)
	}
	if !r.loaded["dev.handline.dev.handline.daemon"] || r.loaded["dev.handline.dev.handline.pc"] {
		t.Fatalf("off touches only its own team: %v", r.loaded)
	}
}

func TestOffKeepsTheFolderAndOnAgainKeepsTheSnapshotUnlessFresh(t *testing.T) {
	r := newRig(t)
	if _, err := r.On("handline.daemon", OnOpts{}); err != nil {
		t.Fatal(err)
	}
	devDB := filepath.Join(r.UserHome, ".handline-dev", "handline.daemon", "db.sqlite")
	dev, err := store.Open(devDB)
	if err != nil {
		t.Fatal(err)
	}
	dev.Exec("INSERT INTO agent_state(agent, state, updated) VALUES('made.in.dev', 'idle', 1)")
	dev.Close()
	has := func() bool {
		d, _ := store.Open(devDB)
		defer d.Close()
		var n int
		d.QueryRow("SELECT COUNT(*) FROM agent_state WHERE agent = 'made.in.dev'").Scan(&n)
		return n == 1
	}
	if err := r.Off("handline.daemon"); err != nil {
		t.Fatal(err)
	}
	if len(r.loaded) != 0 {
		t.Fatalf("off unloads both jobs: %v", r.loaded)
	}
	if _, err := os.Stat(filepath.Join(r.UserHome, "Library", "LaunchAgents", "dev.handline.dev.handline.daemon.plist")); !os.IsNotExist(err) {
		t.Fatalf("off removes the plists: %v", err)
	}
	if _, err := r.On("handline.daemon", OnOpts{}); err != nil || !has() {
		t.Fatalf("on again keeps what the instance wrote: %v", err)
	}
	r.Off("handline.daemon")
	if _, err := r.On("handline.daemon", OnOpts{Fresh: true}); err != nil || has() {
		t.Fatalf("fresh takes the snapshot again: %v", err)
	}
}

func TestRebuildBuildsAgainAndRestartsTheJobs(t *testing.T) {
	r := newRig(t)
	if _, err := r.Rebuild("handline.daemon"); err == nil || !strings.Contains(err.Error(), "`hl dev on handline.daemon`") {
		t.Fatalf("rebuild before on: %v", err)
	}
	if _, err := r.On("handline.daemon", OnOpts{}); err != nil {
		t.Fatal(err)
	}
	r.sha = "def5678"
	in, err := r.Rebuild("handline.daemon")
	if err != nil || in.Sha != "def5678" {
		t.Fatalf("rebuild: %+v %v", in, err)
	}
	if got, _ := r.Read("handline.daemon"); got.Sha != "def5678" {
		t.Fatalf("instance.json keeps the new sha: %+v", got)
	}
	if b, _ := os.ReadFile(filepath.Join(in.Home, "bin", "handlined")); string(b) != "binary def5678" {
		t.Fatalf("binary %q", b)
	}
	if r.count("launchctl kickstart -k gui/501/dev.handline.dev.handline.daemon") != 2 {
		t.Fatalf("both jobs restart onto the new binary: %v", r.calls)
	}
}

func TestABootstrapThatFailsLeavesNothingLoaded(t *testing.T) {
	r := newRig(t)
	launchctl := r.Launchctl
	r.Launchctl = func(args ...string) (string, error) {
		if args[0] == "bootstrap" && strings.HasSuffix(args[2], ".gateway.plist") {
			return "Bootstrap failed: 5", errors.New("exit status 5")
		}
		return launchctl(args...)
	}
	if _, err := r.On("handline.daemon", OnOpts{}); err == nil || !strings.Contains(err.Error(), "Bootstrap failed: 5") {
		t.Fatalf("%v", err)
	}
	if len(r.loaded) != 0 {
		t.Fatalf("the daemon job is taken down again: %v", r.loaded)
	}
}

func same(a, b Instance) bool {
	return a.Team == b.Team && a.Home == b.Home && a.Worktree == b.Worktree && a.Branch == b.Branch && a.Sha == b.Sha &&
		a.Socket == b.Socket && a.Gateway == b.Gateway && strings.Join(a.Labels, " ") == strings.Join(b.Labels, " ")
}

func snapshotDir(t *testing.T, dir string) map[string][]byte {
	t.Helper()
	out := map[string][]byte{}
	filepath.Walk(dir, func(p string, info os.FileInfo, err error) error {
		if err == nil && !info.IsDir() {
			out[p], _ = os.ReadFile(p)
		}
		return nil
	})
	return out
}
