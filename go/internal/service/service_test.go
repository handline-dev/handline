package service

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"
)

// rig is a scratch home with a fake launchd and one daemon process: launchd starts the binary current names, daemon.upgrade execs
// another in the same process (the pid stays), and a daemon that does not answer dies and is started again from current, as
// KeepAlive does. Each daemon is a script that prints its version, and answers the probe with it unless it is broken.
type rig struct {
	t       *testing.T
	s       *Service
	root    string
	loaded  bool
	exe     string // what the daemon process runs; "" when there is none
	pid     int
	calls   []string
	refuse  error           // what daemon.upgrade answers instead of taking the binary
	failing map[string]bool // versions whose daemon does not come up, whatever their bytes
}

func newRig(t *testing.T) *rig {
	root := t.TempDir()
	tools := filepath.Join(root, "tools")
	os.MkdirAll(tools, 0o755)
	os.WriteFile(filepath.Join(tools, "claude"), []byte("#!/bin/sh\n"), 0o755)
	t.Setenv("PATH", tools+":"+os.Getenv("PATH"))
	r := &rig{t: t, root: root, failing: map[string]bool{}}
	r.s = &Service{
		Home: filepath.Join(root, "home"), Label: "test.handline.service", Bin: filepath.Join(root, "bin"),
		Plist: filepath.Join(root, "LaunchAgents", "test.handline.service.plist"), Wait: 300 * time.Millisecond,
		Version: builtVersion,
		Launchctl: func(args ...string) (string, error) {
			r.calls = append(r.calls, args[0])
			switch args[0] {
			case "print":
				if !r.loaded {
					return "not found", errors.New("exit 113")
				}
			case "bootstrap":
				r.loaded = true
				r.start()
			case "bootout":
				r.loaded, r.exe = false, ""
			case "kickstart":
				r.start()
			}
			return "", nil
		},
		Probe: func() (string, error) {
			if r.exe == "" {
				return "", errors.New("not running")
			}
			out, err := exec.Command(r.exe, "-health").Output()
			v := strings.TrimSpace(string(out))
			if err != nil || r.failing[v] {
				r.start() // it exits, and launchd starts the job again
				return "", errors.New("not running")
			}
			return v, nil
		},
		Handover: func(exe, sha string) error {
			r.calls = append(r.calls, "handover")
			if r.exe == "" {
				return errors.New("not running")
			}
			if r.refuse != nil {
				return r.refuse
			}
			if got, err := builtVersion(exe); err != nil || (got != sha && !strings.Contains(got, "+"+sha)) {
				return errors.New("refused: not that build")
			}
			r.exe = exe
			return nil
		},
	}
	return r
}

// start is launchd starting the job's program: the binary current names then, which keeps running when the link moves.
func (r *rig) start() {
	r.exe, r.pid = "", r.pid+1
	if p, err := filepath.EvalSymlinks(filepath.Join(r.s.Bin, current, "handlined")); err == nil && r.loaded {
		r.exe = p
	}
}

func (r *rig) count(call string) int {
	n := 0
	for _, c := range r.calls {
		if c == call {
			n++
		}
	}
	return n
}

// build is a folder holding handlined and hl at version v.
func (r *rig) build(v string, broken bool) string {
	dir, _ := os.MkdirTemp(r.root, "build-"+v+"-")
	health := "echo " + v
	if broken {
		health = "exit 1"
	}
	script := "#!/bin/sh\nif [ \"$1\" = -version ]; then echo " + v + "; exit 0; fi\n" + health + "\n"
	os.WriteFile(filepath.Join(dir, "handlined"), []byte(script), 0o755)
	os.WriteFile(filepath.Join(dir, "hl"), []byte("#!/bin/sh\n"), 0o755)
	return dir
}

func (r *rig) want(cur, prev string, kept ...string) {
	r.t.Helper()
	got := r.s.kept()
	slices.Sort(got)
	slices.Sort(kept)
	if r.s.Current() != cur || r.s.Previous() != prev || !slices.Equal(got, kept) {
		r.t.Fatalf("current %q previous %q kept %v, want %q %q %v", r.s.Current(), r.s.Previous(), got, cur, prev, kept)
	}
}

func TestABrokenUpgradeRollsBackToTheVersionThatRan(t *testing.T) {
	r := newRig(t)
	if msg, err := r.s.Install(r.build("v1", false), false); err != nil {
		t.Fatal(err)
	} else if !strings.Contains(msg, "v1") {
		t.Error(msg)
	}
	r.want("v1", "", "v1")
	plist, _ := os.ReadFile(r.s.Plist)
	if !strings.Contains(string(plist), filepath.Join(r.s.Bin, "current", "handlined")) || !strings.Contains(string(plist), "-home") || !r.loaded {
		t.Fatalf("job: loaded %v\n%s", r.loaded, plist)
	}
	if _, err := r.s.Upgrade(r.build("v2", false), false); err != nil {
		t.Fatal(err)
	}
	r.want("v2", "v1", "v1", "v2")

	_, err := r.s.Upgrade(r.build("v3", true), false)
	if err == nil || !strings.Contains(err.Error(), "rolled back to v2") {
		t.Fatalf("broken upgrade: %v", err)
	}
	r.want("v2", "v1", "v1", "v2")                             // the broken one is not kept to be rolled back to
	if r.count("handover") != 0 || r.count("kickstart") != 3 { // "v2" names no sha for daemon.upgrade: launchd restarts
		t.Errorf("handovers %d kickstarts %d, want 0 3 (v2, v3, back to v2): %v", r.count("handover"), r.count("kickstart"), r.calls)
	}

	if msg, err := r.s.Rollback(); err != nil || msg != "rolled back v2 -> v1" {
		t.Fatalf("rollback: %q %v", msg, err)
	}
	r.want("v1", "v2", "v1", "v2")
	if msg, err := r.s.Rollback(); err != nil || msg != "rolled back v1 -> v2" {
		t.Fatalf("second rollback: %q %v", msg, err)
	}
	if _, err := r.s.Upgrade(r.build("v4", false), false); err != nil {
		t.Fatal(err)
	}
	r.want("v4", "v2", "v2", "v4") // the last two: the one running and the one before it
	if msg, _ := r.s.Upgrade(r.build("v4", false), false); msg != "already on v4" {
		t.Error(msg)
	}
	running, _ := os.ReadFile(filepath.Join(r.s.Bin, "v4", "handlined"))
	other := r.build("v4", false) // a dirty tree at the same commit: same version, other bytes
	os.WriteFile(filepath.Join(other, "hl"), []byte("#!/bin/sh\necho other\n"), 0o755)
	if _, err := r.s.Upgrade(other, false); err == nil || !strings.Contains(err.Error(), "different build") {
		t.Fatalf("a different build of v4: %v", err)
	}
	if now, _ := os.ReadFile(filepath.Join(r.s.Bin, "v4", "handlined")); string(now) != string(running) {
		t.Error("the running version's folder was replaced")
	}
	if _, err := r.s.Install(r.build("v5", true), false); err == nil || !strings.Contains(err.Error(), "rolled back to v4") {
		t.Fatalf("a broken install over v4: %v", err)
	}
	r.want("v4", "v2", "v2", "v4")
	if st := r.s.Status(); !strings.Contains(st, "current: v4") || !strings.Contains(st, "previous: v2") || !strings.Contains(st, "running: v4") || !strings.Contains(st, ": loaded") {
		t.Error(st)
	}
}

func TestAnUpgradeWithoutAVersionChangesNothing(t *testing.T) {
	r := newRig(t)
	if _, err := r.s.Upgrade(r.build("v1", false), false); err == nil || !strings.Contains(err.Error(), "not installed") {
		t.Fatalf("before install: %v", err)
	}
	r.s.Install(r.build("v1", false), false)
	if _, err := r.s.Rollback(); err == nil {
		t.Fatal("rolled back with nothing before v1")
	}
	bad := r.build("v2", false)
	os.WriteFile(filepath.Join(bad, "handlined"), []byte("#!/bin/sh\nexit 1\n"), 0o755)
	if _, err := r.s.Upgrade(bad, false); err == nil {
		t.Fatal("a binary with no version was taken")
	}
	if _, err := r.s.Upgrade(r.build("unknown", false), false); err == nil {
		t.Fatal("an unstamped build was taken")
	}
	missing := r.build("v3", false)
	os.Remove(filepath.Join(missing, "hl"))
	if _, err := r.s.Upgrade(missing, false); err == nil {
		t.Fatal("a folder without hl was taken")
	}
	r.want("v1", "", "v1")
}

func TestTheLiveLabelIsRefusedOnAScratchHome(t *testing.T) {
	r := newRig(t)
	r.s.Label = LiveLabel
	if _, err := r.s.Install(r.build("v1", false), true); err == nil || r.loaded {
		t.Fatalf("installed the live label on %s", r.s.Home)
	}
	if _, err := r.s.Uninstall(); err == nil {
		t.Fatal("uninstalled the live label from a scratch home")
	}
}

func TestARehearsalLabelKeepsItsOwnVersions(t *testing.T) {
	t.Setenv("HANDLINE_SERVICE_LABEL", "dev.handline.rehearsal")
	if s := New(t.TempDir()); filepath.Base(s.Bin) != "bin-dev.handline.rehearsal" || filepath.Base(s.Plist) != "dev.handline.rehearsal.plist" {
		t.Errorf("%s %s", s.Bin, s.Plist)
	}
}

func TestAFailedUpgradeToTheKeptVersionKeepsIt(t *testing.T) {
	r := newRig(t)
	r.s.Install(r.build("v1", false), false)
	r.s.Upgrade(r.build("v2", false), false)
	r.failing["v1"] = true                            // v1 no longer starts, say on a schema v2 changed
	_, err := r.s.Upgrade(r.build("v1", false), true) // the same bytes as the kept v1
	if err == nil || !strings.Contains(err.Error(), "rolled back to v2") {
		t.Fatalf("upgrade to the kept v1: %v", err)
	}
	r.want("v2", "v1", "v1", "v2") // v1's folder is the only copy to roll back to: it stays
}

func TestUpgradeRefusesAVersionThatIsNotNewer(t *testing.T) {
	r := newRig(t)
	r.s.Install(r.build("1.2.0+aaa", false), false)
	for _, v := range []string{"1.1.9+bbb", "1.2.0+bbb", "1.2+ccc"} {
		if _, err := r.s.Upgrade(r.build(v, false), false); err == nil || !strings.Contains(err.Error(), "not newer") {
			t.Fatalf("%s: %v", v, err)
		}
	}
	r.want("1.2.0+aaa", "", "1.2.0+aaa")
	if _, err := r.s.Upgrade(r.build("1.10.0+ddd", false), false); err != nil {
		t.Fatal(err)
	}
	if _, err := r.s.Upgrade(r.build("1.9.0+eee", false), true); err != nil {
		t.Fatalf("--force: %v", err)
	}
	r.want("1.9.0+eee", "1.10.0+ddd", "1.9.0+eee", "1.10.0+ddd")
}

func TestUpgradeTakesAHandlineApp(t *testing.T) {
	r := newRig(t)
	r.s.Install(r.build("1.0.0", false), false)
	app := filepath.Join(r.root, "Handline.app")
	os.MkdirAll(filepath.Join(app, "Contents", "Resources"), 0o755)
	if err := os.Rename(r.build("1.1.0", false), filepath.Join(app, "Contents", "Resources", "bin")); err != nil {
		t.Fatal(err)
	}
	if _, err := r.s.Upgrade(app, false); err != nil {
		t.Fatal(err)
	}
	r.want("1.1.0", "1.0.0", "1.0.0", "1.1.0")
}

func TestStatusJSON(t *testing.T) {
	r := newRig(t)
	if got := r.s.StatusJSON(); got != `{"current":"","installed":false,"path":"`+filepath.Join(r.s.Bin, current)+`","pid":0,"previous":"","running":false}` {
		t.Fatal(got)
	}
	r.s.Install(r.build("1.0.0", false), false)
	r.s.Launchctl = func(args ...string) (string, error) { return "state = running\n\tpid = 4242\n", nil }
	if got := r.s.StatusJSON(); got != `{"current":"1.0.0","installed":true,"path":"`+filepath.Join(r.s.Bin, current)+`","pid":4242,"previous":"","running":true}` {
		t.Fatal(got)
	}
}

func TestUpgradeBetweenUnstampedBuildsIsNotOrdered(t *testing.T) {
	r := newRig(t)
	r.s.Install(r.build("93c3e5b83", false), false)
	if _, err := r.s.Upgrade(r.build("3f9a2b1", false), false); err != nil {
		t.Fatalf("digit-leading sha: %v", err)
	}
	if _, err := r.s.Upgrade(r.build("1.0.0", false), false); err != nil {
		t.Fatalf("sha to release: %v", err)
	}
}

// handWritten is a job under the label written by hand: another program, flags the daemon now defaults or ignores, and the PATH
// and claude the founder's shell had.
func (r *rig) handWritten(program string) (claude, path string) {
	claude = filepath.Join(r.root, "shell-bin", "claude")
	os.MkdirAll(filepath.Dir(claude), 0o755)
	os.WriteFile(claude, []byte("#!/bin/sh\n"), 0o755)
	path = filepath.Dir(claude) + ":/opt/homebrew/bin:/usr/bin:/bin"
	os.MkdirAll(filepath.Dir(r.s.Plist), 0o755)
	os.WriteFile(r.s.Plist, []byte(`<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>test.handline.service</string>
	<key>ProgramArguments</key>
	<array>
		<string>`+program+`</string>
		<string>-allow-real-home</string>
		<string>-claude</string>
		<string>`+claude+`</string>
		<string>-terminal-tick</string>
		<string>-land-repo</string>
		<string>/Users/someone/repo</string>
	</array>
	<key>EnvironmentVariables</key>
	<dict>
		<key>PATH</key>
		<string>`+path+`</string>
	</dict>
	<key>KeepAlive</key>
	<true/>
</dict>
</plist>
`), 0o644)
	return claude, path
}

func TestEnsureTakesOverAHandWrittenJobOnce(t *testing.T) {
	r := newRig(t)
	old := r.build("v0", false)
	claude, path := r.handWritten(filepath.Join(old, "handlined"))
	r.loaded, r.exe, r.pid = true, filepath.Join(old, "handlined"), 1
	t.Setenv("PATH", "/usr/bin:/bin") // an app opened from the Finder: no claude on its PATH
	app := r.build("v1", false)

	if got := r.s.Ensure(app); got != (Ensured{State: "started", Version: "v1", Reason: "the job file was not this one"}) {
		t.Fatalf("%+v", got)
	}
	r.want("v1", "", "v1")
	plist, _ := os.ReadFile(r.s.Plist)
	for _, want := range []string{"<string>" + filepath.Join(r.s.Bin, "current", "handlined") + "</string>", "<string>-claude</string><string>" + claude + "</string>", "<key>PATH</key><string>" + path + "</string>"} {
		if !strings.Contains(string(plist), want) {
			t.Errorf("the job lacks %s:\n%s", want, plist)
		}
	}
	if strings.Contains(string(plist), "land-repo") || strings.Contains(string(plist), old) {
		t.Errorf("the hand-written job's program or flags are still there:\n%s", plist)
	}
	if want, _ := filepath.EvalSymlinks(filepath.Join(r.s.Bin, "v1", "handlined")); strings.Join(r.calls, " ") != "print print bootout print bootstrap print enable" || r.exe != want {
		t.Fatalf("calls %v, running %s", r.calls, r.exe)
	}

	r.calls = nil
	if got := r.s.Ensure(app); got != (Ensured{State: "running", Version: "v1"}) {
		t.Fatalf("a second ensure: %+v", got)
	}
	if after, _ := os.ReadFile(r.s.Plist); string(after) != string(plist) || strings.Join(r.calls, " ") != "print" || r.pid != 2 {
		t.Fatalf("a second ensure changed something: calls %v pid %d", r.calls, r.pid)
	}
}

func TestEnsureHandsANewVersionToTheRunningDaemon(t *testing.T) {
	r := newRig(t)
	if got := r.s.Ensure(r.build("1.0.0+aaa1111", false)); got.State != "started" || got.Reason != "the job was not loaded" {
		t.Fatalf("first run: %+v", got)
	}
	r.calls = nil
	if got := r.s.Ensure(r.build("1.1.0+bbb2222-dirty", false)); got != (Ensured{State: "upgraded", Version: "1.1.0+bbb2222-dirty", Reason: "from 1.0.0+aaa1111"}) {
		t.Fatalf("%+v", got)
	}
	r.want("1.1.0+bbb2222-dirty", "1.0.0+aaa1111", "1.0.0+aaa1111", "1.1.0+bbb2222-dirty")
	if r.pid != 1 || r.count("handover") != 1 || r.count("kickstart")+r.count("bootout") != 0 {
		t.Fatalf("not handed over in place: pid %d calls %v", r.pid, r.calls)
	}
	if got := r.s.Ensure(r.build("0.9.0+ccc3333", false)); got.State != "upgraded" || r.s.Current() != "0.9.0+ccc3333" {
		t.Fatalf("the app carries an older version, and the daemon follows it: %+v", got)
	}
}

func TestEnsureKeepsTheRunningVersionWhenTheNewOneFails(t *testing.T) {
	r := newRig(t)
	r.s.Ensure(r.build("1+aaaaaaa", false))
	got := r.s.Ensure(r.build("2+bbbbbbb", true))
	if got.State != "kept" || got.Version != "1+aaaaaaa" || !strings.Contains(got.Reason, "rolled back to 1+aaaaaaa") {
		t.Fatalf("%+v", got)
	}
	r.want("1+aaaaaaa", "", "1+aaaaaaa")
	if v, err := r.s.Probe(); v != "1+aaaaaaa" || err != nil {
		t.Fatalf("running %q %v", v, err)
	}

	r.failing["1+aaaaaaa"] = true // and now 1+aaaaaaa will not start either
	if got = r.s.Ensure(r.build("2+bbbbbbb", true)); got.State != "failed" || !strings.Contains(got.Reason, "going back to 1+aaaaaaa failed too") {
		t.Fatalf("nothing answers: %+v", got)
	}
	delete(r.failing, "1+aaaaaaa")
	r.s.Ensure(r.build("1+aaaaaaa", false))

	r.calls = nil
	r.refuse = errors.New("3+ccccccc does not start")
	got = r.s.Ensure(r.build("3+ccccccc", false))
	if got.State != "kept" || !strings.Contains(got.Reason, "3+ccccccc does not start") || r.count("kickstart") != 0 {
		t.Fatalf("a refused handover: %+v %v", got, r.calls)
	}
	r.want("1+aaaaaaa", "2+bbbbbbb", "1+aaaaaaa", "2+bbbbbbb") // as the double failure left them: 3+ccccccc is gone
}

func TestEnsureRestartsAJobWhoseDaemonDoesNotAnswer(t *testing.T) {
	r := newRig(t)
	r.s.Ensure(r.build("v1", false))
	r.exe = "" // crashed, and launchd waits out its throttle
	if got := r.s.Ensure(r.build("v1", false)); got != (Ensured{State: "started", Version: "v1", Reason: "the daemon was not answering"}) || r.count("kickstart") != 1 {
		t.Fatalf("%+v %v", got, r.calls)
	}
	r.exe = ""
	if got := r.s.Ensure(r.build("v2", false)); got.State != "upgraded" || r.count("handover") != 0 || r.count("kickstart") != 2 {
		t.Fatalf("%+v %v", got, r.calls)
	}
	r.want("v2", "v1", "v1", "v2")
}

func TestEnsureFailsWithoutClaudeAndChangesNothing(t *testing.T) {
	r := newRig(t)
	t.Setenv("PATH", "/usr/bin:/bin")
	if got := r.s.Ensure(r.build("v1", false)); got.State != "failed" || !strings.Contains(got.Reason, "claude") || r.loaded {
		t.Fatalf("%+v", got)
	}
	r.want("", "")
	r.s.Label = LiveLabel
	if got := r.s.Ensure(r.build("v1", false)); got.State != "failed" || !strings.Contains(got.Reason, "not the live home") {
		t.Fatalf("the live label on a scratch home: %+v", got)
	}
}

func TestStopUnloadsTheJobAndKeepsItsFile(t *testing.T) {
	r := newRig(t)
	r.s.Ensure(r.build("v1", false))
	if msg, err := r.s.Stop(); err != nil || msg != "stopped test.handline.service" || r.loaded {
		t.Fatalf("%q %v", msg, err)
	}
	if _, err := os.Stat(r.s.Plist); err != nil {
		t.Fatal("the job file went with the stop")
	}
	if msg, _ := r.s.Stop(); msg != "test.handline.service was not running" {
		t.Error(msg)
	}
	if got := r.s.Ensure(r.build("v1", false)); got.State != "started" || got.Reason != "the job was not loaded" || !r.loaded {
		t.Fatalf("the next app launch: %+v", got)
	}
	r.s.Label = LiveLabel
	if _, err := r.s.Stop(); err == nil || !r.loaded {
		t.Fatal("stopped the live label from a scratch home")
	}
}

func TestShaOf(t *testing.T) {
	for v, want := range map[string]string{"1.2.0+abc1234": "abc1234", "1.2.0+abc1234-dirty": "abc1234", "93c3e5b83": "93c3e5b83", "1.2.0": "", "v1": ""} {
		if got := shaOf(v); got != want {
			t.Errorf("%s: %s", v, got)
		}
	}
}

func TestATakeoverThatDoesNotAnswerPutsTheHandWrittenJobBack(t *testing.T) {
	r := newRig(t)
	old := r.build("v0", false)
	r.handWritten(filepath.Join(old, "handlined"))
	hand, _ := os.ReadFile(r.s.Plist)
	r.loaded, r.exe = true, filepath.Join(old, "handlined")
	got := r.s.Ensure(r.build("v1", true))
	if got.State != "kept" || !strings.Contains(got.Reason, "the job that was there is back") {
		t.Fatalf("%+v", got)
	}
	if now, _ := os.ReadFile(r.s.Plist); string(now) != string(hand) || !r.loaded {
		t.Fatalf("the hand-written job is not back (loaded %v):\n%s", r.loaded, now)
	}
	r.want("", "")
}

func TestEnsureFindsClaudeOnTheKeptPathWhenItsFileMoved(t *testing.T) {
	r := newRig(t)
	old := r.build("v0", false)
	claude, _ := r.handWritten(filepath.Join(old, "handlined"))
	moved := filepath.Join(filepath.Dir(claude), "claude")
	os.Rename(claude, claude+".old")
	os.WriteFile(moved, []byte("#!/bin/sh\n"), 0o755) // an update put it back under the same folder on the kept PATH
	os.Remove(claude + ".old")
	os.WriteFile(r.s.Plist, []byte(strings.Replace(mustRead(t, r.s.Plist), "<string>"+claude+"</string>", "<string>/gone/claude</string>", 1)), 0o644)
	t.Setenv("PATH", "/usr/bin:/bin")
	if got := r.s.Ensure(r.build("v1", false)); got.State != "started" {
		t.Fatalf("%+v", got)
	}
	if !strings.Contains(mustRead(t, r.s.Plist), "<string>-claude</string><string>"+moved+"</string>") {
		t.Fatal(mustRead(t, r.s.Plist))
	}
}

func mustRead(t *testing.T, p string) string {
	b, err := os.ReadFile(p)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}
