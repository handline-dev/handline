// Package service is the launchd job that keeps the daemon running, and the daemon versions it can run: each in
// <bin>/<version>/, a current link naming the one the job starts, and the one before it kept for a rollback.
package service

import (
	"errors"
	"fmt"
	"html"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
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
func (s *Service) Current() string {
	v, _ := os.Readlink(filepath.Join(s.Bin, current))
	return v
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

// add copies the binaries in src into a folder named by the version handlined says it is, and gives that version.
func (s *Service) add(src string) (string, error) {
	v, err := s.Version(filepath.Join(src, "handlined"))
	if err != nil || v == "" || v == current || strings.ContainsAny(v, `/\`) || strings.HasPrefix(v, ".") {
		return "", fmt.Errorf("%s/handlined -version gave no usable version (%q, %v)", src, v, err)
	}
	tmp := filepath.Join(s.Bin, ".new-"+v)
	os.RemoveAll(tmp)
	if err := os.MkdirAll(tmp, 0o755); err != nil {
		return "", err
	}
	for _, b := range binaries {
		if err := copyFile(filepath.Join(src, b), filepath.Join(tmp, b)); err != nil {
			os.RemoveAll(tmp)
			return "", err
		}
	}
	dir := filepath.Join(s.Bin, v)
	os.RemoveAll(dir) // a running daemon keeps its open file; the job starts the new copy
	return v, os.Rename(tmp, dir)
}

func copyFile(from, to string) error {
	b, err := os.ReadFile(from)
	if err != nil {
		return err
	}
	return os.WriteFile(to, b, 0o755)
}

// point moves the current link to v in one rename, so the job never finds it missing.
func (s *Service) point(v string) error {
	tmp := filepath.Join(s.Bin, ".current")
	os.Remove(tmp)
	if err := os.Symlink(v, tmp); err != nil {
		return err
	}
	return os.Rename(tmp, filepath.Join(s.Bin, current))
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
	prev := s.Current()
	v, err := s.add(src)
	if err != nil {
		return "", err
	}
	if err := s.point(v); err != nil {
		return "", err
	}
	if err := s.load(body); err != nil {
		return "", err
	}
	if err := s.up(v, s.Wait); err != nil {
		return "", err
	}
	s.prune(v, prev)
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

// Upgrade switches the job to the binaries in src. A version that does not answer within the wait is switched back and removed.
func (s *Service) Upgrade(src string) (string, error) {
	prev := s.Current()
	if prev == "" {
		return "", fmt.Errorf("%s is not installed (handlined service install)", s.Label)
	}
	v, err := s.add(src)
	if err != nil {
		return "", err
	}
	if v == prev {
		return "already on " + v, nil
	}
	if err := s.switchTo(v, s.Wait); err != nil {
		if back := s.switchTo(prev, s.back()); back != nil {
			return "", fmt.Errorf("%v; rolling back to %s failed too: %v", err, prev, back)
		}
		os.RemoveAll(filepath.Join(s.Bin, v))
		return "", fmt.Errorf("%v; rolled back to %s", err, prev)
	}
	s.prune(v, prev)
	return fmt.Sprintf("upgraded %s -> %s", prev, v), nil
}

// Rollback switches the job to the other kept version.
func (s *Service) Rollback() (string, error) {
	cur := s.Current()
	var other string
	for _, v := range s.kept() {
		if v != cur {
			other = v
		}
	}
	if other == "" {
		return "", fmt.Errorf("no version besides %q is kept", cur)
	}
	if err := s.switchTo(other, s.Wait); err != nil {
		if back := s.switchTo(cur, s.back()); back != nil {
			return "", fmt.Errorf("%v; going back to %s failed too: %v", err, cur, back)
		}
		return "", fmt.Errorf("%v; stayed on %s", err, cur)
	}
	return fmt.Sprintf("rolled back %s -> %s", cur, other), nil
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

// back is the wait for switching back from a version that kept exiting: launchd may hold the next start for its throttle.
func (s *Service) back() time.Duration { return s.Wait + throttle*time.Second }

// prune keeps v and the version before it, and removes the rest.
func (s *Service) prune(v, prev string) {
	for _, k := range s.kept() {
		if k != v && k != prev {
			os.RemoveAll(filepath.Join(s.Bin, k))
		}
	}
}

// Status is the job, the version it starts, the versions kept, and what the daemon answers as.
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
	return fmt.Sprintf("%s: %s\ncurrent: %s\nkept: %s\nrunning: %s", s.Label, job, cur, strings.Join(kept, " "), ans)
}

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

// plist is the job: the current version's daemon with the production flags, kept alive and throttled so a crash loop cannot run away.
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
	argv = append(argv, "-claude", claude, "-terminal-tick", "-cutover", "-turns-from-hooks", "-notification-needs-you")
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
