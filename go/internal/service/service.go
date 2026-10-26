// Package service is the launchd job that keeps the daemon running, and the daemon versions it can run: each in
// <bin>/<version>/, a current link naming the one the job starts, and a previous link naming the one before it, for a rollback.
package service

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/hlclient"
	"github.com/handline/handline/go/internal/paths"
)

const (
	LiveLabel  = "dev.orcapipe.daemon"
	unloadWait = 30 * time.Second
	upWait     = 60 * time.Second
	throttle   = 30 // seconds launchd waits between starts of a job that keeps exiting
	current    = "current"
	previous   = "previous"
)

// binaries are what a version folder holds, copied from the folder the installing binary lives in.
var binaries = []string{"handlined", "hl"}

// Service is one home's job. Launchctl, Probe and Version are launchd, the running daemon's version and a binary's own; tests give their own.
type Service struct {
	Home      string
	Label     string
	Bin       string
	Plist     string
	Wait      time.Duration
	Launchctl func(args ...string) (string, error)
	Probe     func() (string, error)
	Version   func(daemon string) (string, error)
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
		Home: home, Label: label, Bin: bin, Wait: upWait,
		Plist: filepath.Join(paths.UserHome(), "Library", "LaunchAgents", label+".plist"),
		Launchctl: func(args ...string) (string, error) {
			out, err := exec.Command("launchctl", args...).CombinedOutput()
			return strings.TrimSpace(string(out)), err
		},
		Probe:   func() (string, error) { return running(home) },
		Version: builtVersion,
	}
}

// running is the version the home's daemon says it runs in its hello, without asking launchd to start it.
func running(home string) (string, error) {
	c := &hlclient.Client{Home: home, Dial: hlclient.Dialer(home, 2*time.Second), Kick: func() error { return errors.New("not running") }}
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
	deadline := time.Now().Add(wait)
	got, err := "", error(nil)
	for {
		if got, err = s.Probe(); err == nil && got == v {
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
	return fmt.Errorf("%s did not come up within %s (%v); see %s", v, wait, err, filepath.Join(s.Home, "logs", "daemon.err.log"))
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
	body, err := s.plist() // built first: a refusal leaves the old file alone
	if err != nil {
		return "", err
	}
	cur, prev := s.Current(), s.Previous()
	v, fresh, err := s.add(src)
	if err != nil {
		return "", err
	}
	if err := s.point(v); err != nil {
		return "", err
	}
	if err := s.load(body); err != nil {
		s.setLink(current, cur)
		s.setLink(previous, prev)
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
		s.Launchctl("bootout", target)
		deadline := time.Now().Add(unloadWait)
		for s.loaded() {
			if time.Now().After(deadline) {
				return fmt.Errorf("the old job was still exiting after %s; launchd still has %s - install again once it is gone", unloadWait, s.Label)
			}
			time.Sleep(200 * time.Millisecond)
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

func (s *Service) switchTo(v string, wait time.Duration) error {
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
	if back := s.switchTo(cur, s.Wait+throttle*time.Second); back != nil {
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

// Uninstall unloads the job and removes its file; the version folders stay.
func (s *Service) Uninstall() (string, error) {
	if s.Label == LiveLabel && !paths.IsLive(s.Home) {
		return "", fmt.Errorf("%s is not the live home, so the live service is not its to remove; nothing was done", s.Home)
	}
	_, target := s.target()
	s.Launchctl("bootout", target)
	if err := os.Remove(s.Plist); err != nil {
		return s.Label + " was not installed", nil
	}
	return "removed " + s.Plist, nil
}

// plist is the job: the current version's daemon (its defaults are the service's), kept alive and throttled so a crash loop cannot run away.
func (s *Service) plist() (string, error) {
	claude, err := exec.LookPath("claude")
	if err != nil {
		return "", fmt.Errorf("no claude binary on PATH for -claude")
	}
	argv := []string{filepath.Join(s.Bin, current, "handlined")}
	if paths.IsLive(s.Home) {
		argv = append(argv, "-allow-real-home")
	} else {
		argv = append(argv, "-home", s.Home)
	}
	argv = append(argv, "-claude", claude)
	var b strings.Builder
	str := func(v string) { fmt.Fprintf(&b, "<string>%s</string>", html.EscapeString(v)) }
	b.WriteString(`<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key>`)
	str(s.Label)
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
	str(filepath.Join(s.Home, "logs", "daemon.out.log"))
	b.WriteString("\n<key>StandardErrorPath</key>")
	str(filepath.Join(s.Home, "logs", "daemon.err.log"))
	b.WriteString("\n<key>WorkingDirectory</key>")
	str(s.Home)
	b.WriteString("\n<key>EnvironmentVariables</key><dict><key>PATH</key>")
	str(os.Getenv("PATH"))
	b.WriteString("<key>HOME</key>")
	str(os.Getenv("HOME"))
	b.WriteString("<key>HANDLINE_HOME</key>")
	str(s.Home)
	b.WriteString("</dict>\n</dict></plist>\n")
	return b.String(), nil
}
