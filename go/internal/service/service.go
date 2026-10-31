// Package service is the launchd job that keeps the daemon running, and the daemon versions it can run: each in
// <bin>/<version>/, a current link naming the one the job starts, and a previous link naming the one before it, for a rollback.
package service

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/handline/handline/go/internal/authority"
	"github.com/handline/handline/go/internal/hlclient"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/sessiontoken"
)

const (
	LiveLabel  = "dev.orcapipe.daemon"
	unloadWait = 30 * time.Second
	upWait     = 60 * time.Second
	gatewayUp  = 30 * time.Second
	throttle   = 30 // seconds launchd waits between starts of a job that keeps exiting
	current    = "current"
	previous   = "previous"
)

// binaries are what a version folder holds, copied from the folder the installing binary lives in.
var binaries = []string{"handlined", "hl"}

// Service is one home's job. Launchctl, Probe, Version and Handover are launchd, the running daemon's version, a binary's own, and
// daemon.upgrade on the running daemon; tests give their own. Ensure also runs the gateway as a second job beside it.
type Service struct {
	Home      string
	Label     string
	Bin       string
	Plist     string
	Wait      time.Duration
	Throttle  time.Duration // how long launchd may hold the next start of a job that kept exiting
	Launchctl func(args ...string) (string, error)
	Probe     func() (string, error)
	Version   func(daemon string) (string, error)
	Handover  func(exe, sha string) error

	Listen, Advertise string                 // the gateway's -listen and -advertise from the app; an empty Listen keeps the gateway job's
	GatewayWait       time.Duration          // how long Ensure waits for the gateway to answer
	GatewayProbe      func() (string, error) // the version the gateway answers on gateway.sock
	GatewayPid        string                 // the pid file every gateway writes, a hand-run one too
}

// New is home's service, labelled HANDLINE_SERVICE_LABEL when set (a rehearsal) and the live job's label otherwise.
func New(home string) *Service {
	label := LiveLabel
	if l, _ := paths.Getenv("SERVICE_LABEL"); l != "" {
		label = l
	}
	support := filepath.Join(paths.UserHome(), "Library", "Application Support", "Handline")
	bin := filepath.Join(support, "bin")
	if label != LiveLabel { // a rehearsal's versions never move the live job's link
		bin = filepath.Join(support, "bin-"+label)
	}
	return &Service{
		Home: home, Label: label, Bin: bin, Wait: upWait, Throttle: throttle * time.Second,
		Plist: filepath.Join(paths.UserHome(), "Library", "LaunchAgents", label+".plist"),
		Launchctl: func(args ...string) (string, error) {
			out, err := exec.Command("launchctl", args...).CombinedOutput()
			return strings.TrimSpace(string(out)), err
		},
		Probe:       func() (string, error) { return running(home, hlclient.Dialer(home, 2*time.Second)) },
		Version:     builtVersion,
		Handover:    func(exe, sha string) error { return handover(home, exe, sha) },
		GatewayWait: gatewayUp,
		GatewayProbe: func() (string, error) {
			gw := filepath.Join(home, "gateway.sock")
			return running(home, func() (net.Conn, error) { return net.DialTimeout("unix", gw, 2*time.Second) })
		},
		GatewayPid: filepath.Join(authority.Root(home), "gateway", "pid"),
	}
}

// handover asks home's daemon, as the founder, to exec exe in its own process (daemon.upgrade): same pid, same arguments.
func handover(home, exe, sha string) error {
	c := &hlclient.Client{Home: home, Key: sessiontoken.New(home).FounderKeyHeld(), Dial: hlclient.Dialer(home, 2*time.Second),
		Kick: func() error { return errors.New("not running") }}
	if ok, said := c.Write("daemon.upgrade", map[string]any{"exe": exe, "sha": sha}); !ok {
		return errors.New(said)
	}
	return nil
}

var shaShape = regexp.MustCompile(`^[0-9a-f]{7,40}$`)

// shaOf is the git sha in a version (a release's <version>+<sha>[-dirty], or a checkout build's bare sha), "" when it names none:
// daemon.upgrade takes only a sha.
func shaOf(v string) string {
	if _, after, ok := strings.Cut(v, "+"); ok {
		v = strings.TrimSuffix(after, "-dirty")
	}
	if !shaShape.MatchString(v) {
		return ""
	}
	return v
}

// running is the version the daemon or gateway dial reaches says it runs in its hello, without asking launchd to start it.
func running(home string, dial func() (net.Conn, error)) (string, error) {
	c := &hlclient.Client{Home: home, Dial: dial, Kick: func() error { return errors.New("not running") }}
	res, err := c.Call("hello", nil)
	if err != nil {
		return "", err
	}
	v, _ := res["daemon"].(string)
	return v, nil
}

func builtVersion(daemon string) (string, error) {
	out, err := exec.Command(daemon, "-version").Output()
	return strings.TrimSpace(string(out)), err
}

func (s *Service) target() (domain, target string) {
	domain = fmt.Sprintf("gui/%d", os.Getuid())
	return domain, domain + "/" + s.Label
}

func (s *Service) loaded() bool {
	_, target := s.target()
	_, err := s.Launchctl("print", target)
	return err == nil
}

// Current is the version the job starts, "" before an install.
func (s *Service) Current() string { return s.link(current) }

// Previous is the version a rollback goes to, "" when there is none.
func (s *Service) Previous() string { return s.link(previous) }

func (s *Service) link(name string) string {
	v, _ := os.Readlink(filepath.Join(s.Bin, name))
	return v
}

// setLink points name at v in one rename, so the job never finds it missing; an empty v removes it.
func (s *Service) setLink(name, v string) error {
	if v == "" {
		os.Remove(filepath.Join(s.Bin, name))
		return nil
	}
	tmp := filepath.Join(s.Bin, "."+name)
	os.Remove(tmp)
	if err := os.Symlink(v, tmp); err != nil {
		return err
	}
	return os.Rename(tmp, filepath.Join(s.Bin, name))
}

// kept is every version folder there, the current one included.
func (s *Service) kept() []string {
	entries, _ := os.ReadDir(s.Bin)
	var vs []string
	for _, e := range entries {
		if e.IsDir() && !strings.HasPrefix(e.Name(), ".") {
			vs = append(vs, e.Name())
		}
	}
	return vs
}

// add copies the binaries in src into a folder named by the version handlined says it is, and gives that version. A folder
// that version already has is kept when it holds the same bytes and refused otherwise: the job may be running it.
func (s *Service) add(src string) (v string, fresh bool, err error) {
	v, err = s.Version(filepath.Join(src, "handlined"))
	if err != nil || v == "" || v == "unknown" || v == current || v == previous || strings.ContainsAny(v, `/\`) || strings.HasPrefix(v, ".") {
		return "", false, fmt.Errorf("%s/handlined -version gave no usable version (%q, %v)", src, v, err)
	}
	dir := filepath.Join(s.Bin, v)
	if _, err := os.Stat(dir); err == nil {
		for _, b := range binaries {
			if !sameFile(filepath.Join(src, b), filepath.Join(dir, b)) {
				return "", false, fmt.Errorf("%s already holds a different build of %s: build again from a clean commit", dir, v)
			}
		}
		return v, false, nil
	}
	tmp := filepath.Join(s.Bin, ".new-"+v)
	os.RemoveAll(tmp)
	if err := os.MkdirAll(tmp, 0o755); err != nil {
		return "", false, err
	}
	for _, b := range binaries {
		if err := copyFile(filepath.Join(src, b), filepath.Join(tmp, b)); err != nil {
			os.RemoveAll(tmp)
			return "", false, err
		}
	}
	return v, true, os.Rename(tmp, dir)
}

func sameFile(a, b string) bool {
	x, err := os.ReadFile(a)
	y, err2 := os.ReadFile(b)
	return err == nil && err2 == nil && bytes.Equal(x, y)
}

func copyFile(from, to string) error {
	b, err := os.ReadFile(from)
	if err != nil {
		return err
	}
	return os.WriteFile(to, b, 0o755)
}

// point moves the current link to v, and the previous link to the version it named.
func (s *Service) point(v string) error {
	if cur := s.Current(); cur != "" && cur != v {
		if err := s.setLink(previous, cur); err != nil {
			return err
		}
	}
	return s.setLink(current, v)
}

// up waits until the daemon answers with version v, for wait.
func (s *Service) up(v string, wait time.Duration) error {
	return waitFor(s.Probe, v, wait, filepath.Join(s.Home, "logs", "daemon.err.log"))
}

// waitFor waits until probe answers with version v, for wait; the error names log.
func waitFor(probe func() (string, error), v string, wait time.Duration, log string) error {
	deadline := time.Now().Add(wait)
	got, err := "", error(nil)
	for {
		if got, err = probe(); err == nil && got == v {
			return nil
		}
		if time.Now().After(deadline) {
			break
		}
		time.Sleep(500 * time.Millisecond)
	}
	if err == nil {
		err = fmt.Errorf("it answers as %s", got)
	}
	return fmt.Errorf("%s did not come up within %s (%v); see %s", v, wait, err, log)
}

func (s *Service) restart() error {
	_, target := s.target()
	if out, err := s.Launchctl("kickstart", "-k", target); err != nil {
		return fmt.Errorf("launchctl kickstart: %s", out)
	}
	return nil
}

// Guard refuses a home this label is not for: the live job only on the live home, and only when asked; any other home needs its own label.
func (s *Service) Guard(allowLive bool) error {
	live := paths.IsLive(s.Home)
	switch {
	case !live && s.Label == LiveLabel:
		return fmt.Errorf("%s is not the live home, and the live service's label would be replaced: set HANDLINE_SERVICE_LABEL for a rehearsal", s.Home)
	case live && !allowLive:
		return fmt.Errorf("%s is the live home: the Go daemon is installed there only with --allow-live-home", s.Home)
	}
	return nil
}

// Install copies the binaries in src in as the current version, writes the job and (re)loads it, and waits for that version to answer.
func (s *Service) Install(src string, allowLive bool) (string, error) {
	if err := s.Guard(allowLive); err != nil {
		return "", err
	}
	if err := os.MkdirAll(filepath.Join(s.Home, "logs"), 0o755); err != nil {
		return "", err
	}
	body, _, err := s.plist(false) // built first: a refusal leaves the old file alone
	if err != nil {
		return "", fmt.Errorf("%w: no claude binary on PATH for -claude", err)
	}
	cur, prev := s.Current(), s.Previous()
	v, fresh, err := s.add(src)
	if err != nil {
		return "", err
	}
	if err := s.startOn(body, v, cur, prev); err != nil {
		return "", err
	}
	if err := s.up(v, s.Wait); err != nil {
		if cur == "" || cur == v {
			return "", err
		}
		return "", s.goBack(cur, prev, v, fresh, err)
	}
	s.prune()
	return fmt.Sprintf("installed %s (%s), label %s", v, s.Plist, s.Label), nil
}

// startOn points current at v and loads the job body onto it; a job that will not load leaves the links as they were.
func (s *Service) startOn(body, v, cur, prev string) error {
	if err := s.point(v); err != nil {
		return err
	}
	if err := s.load(body); err != nil {
		s.setLink(current, cur)
		s.setLink(previous, prev)
		return err
	}
	return nil
}

// unload boots the job out and waits until launchd no longer lists it.
func (s *Service) unload() error {
	_, target := s.target()
	s.Launchctl("bootout", target)
	deadline := time.Now().Add(unloadWait)
	for s.loaded() {
		if time.Now().After(deadline) {
			return fmt.Errorf("the job was still exiting after %s; launchd still has %s", unloadWait, s.Label)
		}
		time.Sleep(200 * time.Millisecond)
	}
	return nil
}

// load writes the job and has launchd take it, the old one gone first; a job that will not load puts the old file back.
func (s *Service) load(body string) error {
	prior, _ := os.ReadFile(s.Plist)
	wasLoaded := s.loaded()
	if err := os.MkdirAll(filepath.Dir(s.Plist), 0o755); err != nil {
		return err
	}
	if err := os.WriteFile(s.Plist, []byte(body), 0o644); err != nil {
		return err
	}
	domain, target := s.target()
	if wasLoaded {
		if err := s.unload(); err != nil {
			return fmt.Errorf("%v - install again once it is gone", err)
		}
	}
	if out, err := s.Launchctl("bootstrap", domain, s.Plist); err != nil {
		if wasLoaded && prior != nil { // a running daemon must not end unsupervised because the new plist would not load
			os.WriteFile(s.Plist, prior, 0o644)
			s.Launchctl("bootstrap", domain, s.Plist)
		}
		if out == "" {
			out = "launchctl bootstrap failed"
		}
		return errors.New(out)
	}
	if !s.loaded() {
		return fmt.Errorf("launchctl bootstrap succeeded but launchd does not list %s", s.Label)
	}
	s.Launchctl("enable", target)
	return nil
}

// BinDir is where the binaries are for src: src itself, or the bin folder of the Handline.app src is.
func BinDir(src string) string {
	for _, d := range []string{src, filepath.Join(src, "Contents", "Resources", "bin")} {
		if _, err := os.Stat(filepath.Join(d, "handlined")); err == nil {
			return d
		}
	}
	return src
}

// number is the dotted numbers a version starts with ("1.2.10+abc" is 1, 2, 10), nil when there are not at least two: a bare git
// sha is no version, whatever digit it starts with.
func number(v string) []int {
	var n []int
	for _, p := range strings.Split(strings.TrimPrefix(v, "v"), ".") {
		end := 0
		for end < len(p) && p[end] >= '0' && p[end] <= '9' {
			end++
		}
		if end == 0 {
			break
		}
		x, _ := strconv.Atoi(p[:end])
		n = append(n, x)
		if end < len(p) {
			break
		}
	}
	if len(n) < 2 {
		return nil
	}
	return n
}

// newer reports whether v is above cur by their numbers; versions with no number (a dev build) cannot be ordered and pass.
func newer(v, cur string) bool {
	a, b := number(v), number(cur)
	if a == nil || b == nil {
		return true
	}
	for len(a) < len(b) {
		a = append(a, 0)
	}
	for len(b) < len(a) {
		b = append(b, 0)
	}
	return slices.Compare(a, b) > 0
}

// Upgrade switches the job to the binaries in src (a folder holding them, or a Handline.app). A version that is not above the
// current one is refused unless force; one that does not answer within the wait is switched back and removed.
func (s *Service) Upgrade(src string, force bool) (string, error) {
	cur, prev := s.Current(), s.Previous()
	if cur == "" {
		return "", fmt.Errorf("%s is not installed (handlined service install)", s.Label)
	}
	src = BinDir(src)
	if !force {
		if v, err := s.Version(filepath.Join(src, "handlined")); err == nil && v != cur && !newer(v, cur) {
			return "", fmt.Errorf("%s is not newer than %s; --force installs it anyway", v, cur)
		}
	}
	v, fresh, err := s.add(src)
	if err != nil {
		return "", err
	}
	if v == cur {
		return "already on " + v, nil
	}
	if err := s.switchTo(v, s.Wait); err != nil {
		return "", s.goBack(cur, prev, v, fresh, err)
	}
	s.prune()
	return fmt.Sprintf("upgraded %s -> %s", cur, v), nil
}

// Rollback switches the job to the previous version, which then becomes the one a second rollback returns to.
func (s *Service) Rollback() (string, error) {
	cur, prev := s.Current(), s.Previous()
	if _, err := os.Stat(filepath.Join(s.Bin, prev)); prev == "" || err != nil {
		return "", fmt.Errorf("no version before %q is kept", cur)
	}
	if err := s.switchTo(prev, s.Wait); err != nil {
		return "", s.goBack(cur, prev, prev, false, err)
	}
	return fmt.Sprintf("rolled back %s -> %s", cur, prev), nil
}

// switchTo moves the daemon onto v. A daemon that answers is handed v in its own process (daemon.upgrade), and current moves once v
// answers; only when none answers, or v names no sha, does current move first and launchd restart the job.
func (s *Service) switchTo(v string, wait time.Duration) error {
	if got, err := s.Probe(); err == nil && (got == v || shaOf(v) != "") {
		if got != v {
			if err := s.Handover(filepath.Join(s.Bin, v, "handlined"), shaOf(v)); err != nil {
				return fmt.Errorf("daemon.upgrade to %s: %v", v, err)
			}
			if err := s.up(v, wait); err != nil {
				return err
			}
		}
		return s.point(v)
	}
	if err := s.point(v); err != nil {
		return err
	}
	if err := s.restart(); err != nil {
		return err
	}
	return s.up(v, wait)
}

// goBack restarts cur after v did not answer, with the links as they were, and removes v when this call added it. The wait is longer:
// launchd may hold the next start of a job that kept exiting for its throttle.
func (s *Service) goBack(cur, prev, v string, fresh bool, err error) error {
	if back := s.switchTo(cur, s.Wait+s.Throttle); back != nil {
		return fmt.Errorf("%v; going back to %s failed too: %v", err, cur, back)
	}
	s.setLink(previous, prev)
	if fresh {
		os.RemoveAll(filepath.Join(s.Bin, v))
	}
	return fmt.Errorf("%v; rolled back to %s", err, cur)
}

// prune removes every version but the current and previous ones.
func (s *Service) prune() {
	for _, k := range s.kept() {
		if k != s.Current() && k != s.Previous() {
			os.RemoveAll(filepath.Join(s.Bin, k))
		}
	}
}

// Status is the job, the version it starts and the one before, the versions kept, and what the daemon answers as.
func (s *Service) Status() string {
	job := "not loaded"
	if s.loaded() {
		job = "loaded"
	}
	cur := s.Current()
	if cur == "" {
		cur = "none installed"
	}
	ans, err := s.Probe()
	if err != nil {
		ans = "not answering"
	}
	kept := s.kept()
	slices.Sort(kept)
	return fmt.Sprintf("%s: %s\ncurrent: %s\nprevious: %s\nkept: %s\nrunning: %s", s.Label, job, cur, s.Previous(), strings.Join(kept, " "), ans)
}

// StatusJSON is Status as one JSON object for the app: installed, running (the job has a process), the current and previous
// versions, and the job's pid (0 when it has none).
func (s *Service) StatusJSON() string {
	_, target := s.target()
	out, _ := s.Launchctl("print", target)
	pid := 0
	if m := pidLine.FindStringSubmatch(out); m != nil {
		pid, _ = strconv.Atoi(m[1])
	}
	b, _ := json.Marshal(map[string]any{
		"installed": s.Current() != "", "running": pid > 0, "current": s.Current(), "previous": s.Previous(), "pid": pid, "path": filepath.Join(s.Bin, current),
	})
	return string(b)
}

var pidLine = regexp.MustCompile(`(?m)^\s*pid = (\d+)`)

// Uninstall unloads the job and the gateway's and removes their files; the version folders stay.
func (s *Service) Uninstall() (string, error) {
	if s.Label == LiveLabel && !paths.IsLive(s.Home) {
		return "", fmt.Errorf("%s is not the live home, so the live service is not its to remove; nothing was done", s.Home)
	}
	gw := s.gatewayJob()
	_, gwTarget := gw.target()
	gw.Launchctl("bootout", gwTarget)
	os.Remove(gw.Plist)
	_, target := s.target()
	s.Launchctl("bootout", target)
	if err := os.Remove(s.Plist); err != nil {
		return s.Label + " was not installed", nil
	}
	return "removed " + s.Plist, nil
}

// Ensured is what Ensure did, as the one JSON line the app shows: state is running (the daemon was on version; at most current moved),
// started (the job was loaded onto version), upgraded, kept (version did not answer and the daemon is back on the one before) or
// failed (reason missing:claude or missing:tmux when the job's PATH lacks one, and nothing was written; gateway:<cause> when the
// daemon runs version but the gateway does not answer).
type Ensured struct {
	State   string `json:"state"`
	Version string `json:"version"`
	Reason  string `json:"reason"`
}

// Ensure makes the job run the binaries in src (a folder holding them, or a Handline.app), as the app does at every launch: it adds
// that version; when launchd does not hold this job file (a first run, or another hand-written job under the label) it writes it and
// loads it onto that version; otherwise a daemon on another version is handed over in place (same pid, agents untouched). A second
// run changes nothing. The version the app carries is the one run, older or not: app and daemon are one unit. Once the daemon runs
// it, the gateway job runs it too (ensureGateway), and Ensure reports only when both answer.
func (s *Service) Ensure(src string) Ensured {
	failed := func(err error) Ensured { return Ensured{State: "failed", Version: s.Current(), Reason: err.Error()} }
	if err := s.Guard(true); err != nil {
		return failed(err)
	}
	body, path, err := s.plist(true) // first: a missing tool fails before anything is written
	if err != nil {
		return failed(err)
	}
	gw := s.gatewayJob()
	gbody, err := s.gatewayPlist(gw, path)
	if err != nil {
		return failed(fmt.Errorf("gateway:%v", err))
	}
	if err := os.MkdirAll(filepath.Join(s.Home, "logs"), 0o755); err != nil {
		return failed(err)
	}
	e := s.ensureDaemon(src, body)
	if e.State == "failed" || e.State == "kept" {
		return e
	}
	if err := s.ensureGateway(gw, gbody, e.Version); err != nil {
		return Ensured{State: "failed", Version: e.Version, Reason: fmt.Sprintf("gateway:%v", err)}
	}
	return e
}

// ensureDaemon is Ensure's daemon half, with the job body already built.
func (s *Service) ensureDaemon(src, body string) Ensured {
	failed := func(err error) Ensured { return Ensured{State: "failed", Version: s.Current(), Reason: err.Error()} }
	cur, prev := s.Current(), s.Previous()
	v, fresh, err := s.add(BinDir(src))
	if err != nil {
		return failed(err)
	}
	back := func(err error) Ensured {
		if cur == "" || cur == v {
			return failed(err)
		}
		err = s.goBack(cur, prev, v, fresh, err)
		if got, e := s.Probe(); e != nil || got != cur {
			return failed(err)
		}
		return Ensured{State: "kept", Version: cur, Reason: err.Error()}
	}
	prior, _ := os.ReadFile(s.Plist)
	if wasLoaded := s.loaded(); string(prior) != body || !wasLoaded {
		why := "the job was not loaded"
		if wasLoaded {
			why = "the job file was not this one"
		}
		if err := s.startOn(body, v, cur, prev); err != nil {
			return failed(err)
		}
		if err := s.up(v, s.Wait); err != nil {
			if cur == "" && wasLoaded && prior != nil { // a job this service did not write: put it back as it was
				return s.restore(prior, v, err)
			}
			return back(err)
		}
		s.prune()
		return Ensured{State: "started", Version: v, Reason: why}
	}
	got, perr := s.Probe()
	if perr == nil && got == v && cur == v {
		return Ensured{State: "running", Version: v}
	}
	if err := s.switchTo(v, s.Wait); err != nil {
		return back(err)
	}
	s.prune()
	switch {
	case perr != nil:
		return Ensured{State: "started", Version: v, Reason: "the daemon was not answering"}
	case got == v: // handed over before, and current did not follow
		return Ensured{State: "running", Version: v, Reason: "current moved to it"}
	}
	return Ensured{State: "upgraded", Version: v, Reason: "from " + got}
}

// gatewayJob is the gateway's job beside this one: dev.orcapipe.gateway for the live daemon's, <label>.gateway for a rehearsal's.
func (s *Service) gatewayJob() *Service {
	g := *s
	g.Label = strings.TrimSuffix(s.Label, ".daemon") + ".gateway"
	g.Plist = filepath.Join(filepath.Dir(s.Plist), g.Label+".plist")
	return &g
}

// gatewayPlist is the gateway job on the current version: Listen and Advertise when the app gives Listen, otherwise the ones the
// gateway job there names.
func (s *Service) gatewayPlist(gw *Service, path string) (string, error) {
	listen, advertise := s.Listen, s.Advertise
	if listen == "" {
		b, _ := os.ReadFile(gw.Plist)
		listen, advertise = argAfter(b, "-listen"), argAfter(b, "-advertise")
	}
	if listen == "" {
		return "", errors.New("no address to listen on: pass --listen")
	}
	argv := append(s.program(), "-gateway", "-listen", listen)
	if advertise != "" {
		argv = append(argv, "-advertise", advertise)
	}
	return s.job(gw.Label, argv, path, "gateway"), nil
}

// argAfter is the argument after flag in a job file, "" when it has none.
func argAfter(job []byte, flag string) string {
	m := regexp.MustCompile(`<string>` + regexp.QuoteMeta(flag) + `</string>\s*<string>([^<]*)</string>`).FindSubmatch(job)
	if m == nil {
		return ""
	}
	return html.UnescapeString(string(m[1]))
}

// ensureGateway has the gateway job run v and waits for it to answer. A job launchd does not hold as written is (re)loaded, after
// a gateway that no job runs is stopped (it holds the port and the pid file); a loaded one on another version is restarted, as it
// holds no agents.
func (s *Service) ensureGateway(gw *Service, body, v string) error {
	prior, _ := os.ReadFile(gw.Plist)
	if loaded := gw.loaded(); string(prior) != body || !loaded {
		if !loaded {
			if err := s.stopStrayGateway(); err != nil {
				return err
			}
		}
		if err := gw.load(body); err != nil {
			return err
		}
	} else if got, err := s.GatewayProbe(); err != nil || got != v {
		if err := gw.restart(); err != nil {
			return err
		}
	}
	return waitFor(s.GatewayProbe, v, s.GatewayWait, filepath.Join(s.Home, "logs", "gateway.err.log"))
}

// stopStrayGateway stops the gateway the pid file names when it still runs: one started by hand, outside any job. Only a process
// that is a handlined -gateway is stopped, since a pid can be reused.
func (s *Service) stopStrayGateway() error {
	b, err := os.ReadFile(s.GatewayPid)
	if err != nil {
		return nil
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(b)))
	if err != nil || pid <= 0 {
		return nil
	}
	cmd, _ := exec.Command("ps", "-p", strconv.Itoa(pid), "-o", "command=").Output()
	if !strings.Contains(string(cmd), "handlined") || !strings.Contains(string(cmd), "-gateway") {
		return nil
	}
	if err := syscall.Kill(pid, syscall.SIGTERM); err != nil {
		return fmt.Errorf("stopping the gateway that runs outside the job (pid %d): %v", pid, err)
	}
	deadline := time.Now().Add(unloadWait)
	for alive(pid) {
		if time.Now().After(deadline) {
			return fmt.Errorf("the gateway that runs outside the job (pid %d) did not exit within %s", pid, unloadWait)
		}
		time.Sleep(200 * time.Millisecond)
	}
	return nil
}

func alive(pid int) bool { return syscall.Kill(pid, 0) == nil }

// restore puts back the job file another hand wrote, after v did not answer under this one, and removes v.
func (s *Service) restore(prior []byte, v string, err error) Ensured {
	s.setLink(current, "")
	os.RemoveAll(filepath.Join(s.Bin, v))
	if back := s.load(string(prior)); back != nil {
		return Ensured{State: "failed", Reason: fmt.Sprintf("%v; putting the job that was there back failed too: %v", err, back)}
	}
	return Ensured{State: "kept", Reason: fmt.Sprintf("%v; the job that was there is back", err)}
}

// Stop unloads the job and the gateway's and leaves their files, so the next Ensure (the next app launch) or login starts them again.
func (s *Service) Stop() (string, error) {
	if s.Label == LiveLabel && !paths.IsLive(s.Home) {
		return "", fmt.Errorf("%s is not the live home, so the live service is not its to stop; nothing was done", s.Home)
	}
	if gw := s.gatewayJob(); gw.loaded() {
		if err := gw.unload(); err != nil {
			return "", err
		}
	}
	if !s.loaded() {
		return s.Label + " was not running", nil
	}
	if err := s.unload(); err != nil {
		return "", err
	}
	return "stopped " + s.Label, nil
}

var (
	plistPath   = regexp.MustCompile(`<key>PATH</key>\s*<string>([^<]*)</string>`)
	plistClaude = regexp.MustCompile(`<string>-(?:claude|structured-cli)</string>\s*<string>([^<]*)</string>`)
)

// keptEnv is the claude binary and PATH the job file there now runs with, when it names them: an app opened from the Finder has neither
// on its own PATH, and the job that works today has both.
func (s *Service) keptEnv() (claude, path string) {
	b, _ := os.ReadFile(s.Plist)
	if m := plistClaude.FindSubmatch(b); m != nil {
		claude = html.UnescapeString(string(m[1]))
	}
	if m := plistPath.FindSubmatch(b); m != nil {
		path = html.UnescapeString(string(m[1]))
	}
	return claude, path
}

// missing is a tool the job needs that its PATH lacks; its text is the reason the app reads to show the install command.
type missing string

func (m missing) Error() string { return "missing:" + string(m) }

// lookPath is name's executable in the first folder of path that holds one, "" when none does.
func lookPath(name, path string) string {
	for _, d := range filepath.SplitList(path) {
		if f := filepath.Join(d, name); d != "" {
			if fi, err := os.Stat(f); err == nil && !fi.IsDir() && fi.Mode()&0o111 != 0 {
				return f
			}
		}
	}
	return ""
}

// plist is the job: the current version's daemon (its defaults are the service's), kept alive and throttled so a crash loop cannot
// run away. keep takes the claude binary and PATH from the job file there, when it names them, over this process's. It fails with
// missing when that PATH has no claude, or, with keep (the app's ensure), no tmux. path is the PATH the job runs with.
func (s *Service) plist(keep bool) (body, path string, err error) {
	claude, path := "", os.Getenv("PATH")
	if keep {
		var keptPath string
		claude, keptPath = s.keptEnv()
		if keptPath != "" {
			path = keptPath
		}
		if _, err := os.Stat(claude); err != nil {
			claude = ""
		}
	}
	if claude == "" {
		claude = lookPath("claude", path)
	}
	if claude == "" {
		return "", "", missing("claude")
	}
	if keep && lookPath("tmux", path) == "" { // the daemon's agents run in tmux; only the app's ensure checks it
		return "", "", missing("tmux")
	}
	return s.job(s.Label, append(s.program(), "-claude", claude), path, "daemon"), path, nil
}

// program is the current version's handlined and the home it runs on.
func (s *Service) program() []string {
	argv := []string{filepath.Join(s.Bin, current, "handlined")}
	if paths.IsLive(s.Home) {
		return append(argv, "-allow-real-home")
	}
	return append(argv, "-home", s.Home)
}

// job is a launchd job file running argv with PATH path, logging to <home>/logs/<logName>.{out,err}.log.
func (s *Service) job(label string, argv []string, path, logName string) string {
	var b strings.Builder
	str := func(v string) { fmt.Fprintf(&b, "<string>%s</string>", html.EscapeString(v)) }
	b.WriteString(`<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key>`)
	str(label)
	b.WriteString("\n<key>ProgramArguments</key><array>")
	for _, a := range argv {
		str(a)
	}
	b.WriteString(`</array>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
<key>ThrottleInterval</key><integer>` + fmt.Sprint(throttle) + `</integer>
<key>SoftResourceLimits</key><dict><key>NumberOfFiles</key><integer>4096</integer></dict>
<key>StandardOutPath</key>`)
	str(filepath.Join(s.Home, "logs", logName+".out.log"))
	b.WriteString("\n<key>StandardErrorPath</key>")
	str(filepath.Join(s.Home, "logs", logName+".err.log"))
	b.WriteString("\n<key>WorkingDirectory</key>")
	str(s.Home)
	b.WriteString("\n<key>EnvironmentVariables</key><dict><key>PATH</key>")
	str(path)
	b.WriteString("<key>HOME</key>")
	str(os.Getenv("HOME"))
	b.WriteString("<key>HANDLINE_HOME</key>")
	str(s.Home)
	b.WriteString("</dict>\n</dict></plist>\n")
	return b.String()
}
