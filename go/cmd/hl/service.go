package main

import (
	"fmt"
	"html"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/hlclient"
	"github.com/handline/handline/go/internal/paths"
)

const (
	liveServiceLabel = "dev.orcapipe.daemon"
	unloadWait       = 30 * time.Second
)

// service is `hl service install | uninstall`: the launchd job that keeps the daemon running.
func service(c *hlclient.Client, args []string) (int, bool) {
	if len(args) == 0 {
		return 0, false
	}
	switch args[0] {
	case "install":
		p, ok := parse(args[1:], nil, []string{"--allow-live-home"}, nil)
		if !ok || len(p.pos) != 0 {
			return 0, false
		}
		return finish(installService(c.Home, p.flags["--allow-live-home"])), true
	case "uninstall":
		if len(args) != 1 {
			return 0, false
		}
		return finish(uninstallService(c.Home)), true
	}
	return 0, false
}

func finish(msg string, err error) int {
	if err != nil {
		fmt.Println(err)
		return 1
	}
	fmt.Println(msg)
	return 0
}

func serviceLabel() string {
	if l, _ := paths.Getenv("SERVICE_LABEL"); l != "" {
		return l
	}
	return liveServiceLabel
}

func plistPath() string {
	return filepath.Join(paths.UserHome(), "Library", "LaunchAgents", serviceLabel()+".plist")
}

func launchctl(args ...string) (string, error) {
	out, err := exec.Command("launchctl", args...).CombinedOutput()
	return strings.TrimSpace(string(out)), err
}

func serviceTarget() (domain, target string) {
	domain = fmt.Sprintf("gui/%d", os.Getuid())
	return domain, domain + "/" + serviceLabel()
}

func loaded() bool {
	_, target := serviceTarget()
	_, err := launchctl("print", target)
	return err == nil
}

// repoRoot is the checkout this binary was built in (it lives at <checkout>/go/hl).
func repoRoot() string {
	exe, err := os.Executable()
	if err != nil {
		return ""
	}
	return filepath.Dir(filepath.Dir(paths.Resolved(exe)))
}

// plist is the job: the Go daemon with the production flags, kept alive and throttled so a crash loop cannot run away.
func plist(home string, allowLive bool) (string, error) {
	root := repoRoot()
	daemon := filepath.Join(root, "go", "handlined")
	if _, err := os.Stat(daemon); err != nil {
		return "", fmt.Errorf("%s is not built (tools/build-go.sh)", daemon)
	}
	claude, err := exec.LookPath("claude")
	if err != nil {
		return "", fmt.Errorf("no claude binary on PATH for -claude")
	}
	live := paths.IsLive(home)
	switch {
	case !live && serviceLabel() == liveServiceLabel:
		return "", fmt.Errorf("%s is not the live home, and the live service's label would be replaced: set HANDLINE_SERVICE_LABEL for a rehearsal", home)
	case live && !allowLive:
		return "", fmt.Errorf("%s is the live home: the Go daemon is installed there only with --allow-live-home", home)
	}
	argv := []string{daemon}
	if live {
		argv = append(argv, "-allow-real-home")
	} else {
		argv = append(argv, "-home", home)
	}
	argv = append(argv, "-claude", claude, "-terminal-tick", "-cutover")
	var b strings.Builder
	str := func(s string) { fmt.Fprintf(&b, "<string>%s</string>", html.EscapeString(s)) }
	b.WriteString(`<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key>`)
	str(serviceLabel())
	b.WriteString("\n<key>ProgramArguments</key><array>")
	for _, a := range argv {
		str(a)
	}
	b.WriteString(`</array>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
<key>ThrottleInterval</key><integer>30</integer>
<key>SoftResourceLimits</key><dict><key>NumberOfFiles</key><integer>4096</integer></dict>
<key>StandardOutPath</key>`)
	str(filepath.Join(home, "logs", "daemon.out.log"))
	b.WriteString("\n<key>StandardErrorPath</key>")
	str(filepath.Join(home, "logs", "daemon.err.log"))
	b.WriteString("\n<key>WorkingDirectory</key>")
	str(root)
	b.WriteString("\n<key>EnvironmentVariables</key><dict><key>PATH</key>")
	str(os.Getenv("PATH"))
	b.WriteString("<key>HOME</key>")
	str(os.Getenv("HOME"))
	b.WriteString("<key>HANDLINE_HOME</key>")
	str(home)
	b.WriteString("</dict>\n</dict></plist>\n")
	return b.String(), nil
}

func installService(home string, allowLive bool) (string, error) {
	if err := os.MkdirAll(filepath.Join(home, "logs"), 0o755); err != nil {
		return "", err
	}
	body, err := plist(home, allowLive) // built first: a refusal leaves the old file alone
	if err != nil {
		return "", err
	}
	path := plistPath()
	prior, _ := os.ReadFile(path)
	wasLoaded := loaded()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return "", err
	}
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		return "", err
	}
	domain, target := serviceTarget()
	if wasLoaded {
		launchctl("bootout", target)
		deadline := time.Now().Add(unloadWait)
		for loaded() {
			if time.Now().After(deadline) {
				return "", fmt.Errorf("the old job was still exiting after %s; launchd still has %s - run `hl service install` again once it is gone", unloadWait, serviceLabel())
			}
			time.Sleep(200 * time.Millisecond)
		}
	}
	if out, err := launchctl("bootstrap", domain, path); err != nil {
		if wasLoaded && prior != nil { // a running daemon must not end unsupervised because the new plist would not load
			os.WriteFile(path, prior, 0o644)
			launchctl("bootstrap", domain, path)
		}
		if out == "" {
			out = "launchctl bootstrap failed"
		}
		return "", fmt.Errorf("%s", out)
	}
	if !loaded() {
		return "", fmt.Errorf("launchctl bootstrap succeeded but launchd does not list %s", serviceLabel())
	}
	launchctl("enable", target)
	return fmt.Sprintf("installed %s, label %s", path, serviceLabel()), nil
}

func uninstallService(home string) (string, error) {
	if serviceLabel() == liveServiceLabel && !paths.IsLive(home) {
		return "", fmt.Errorf("%s is not the live home, so the live service is not its to remove; nothing was done", home)
	}
	_, target := serviceTarget()
	launchctl("bootout", target)
	path := plistPath()
	if err := os.Remove(path); err != nil {
		return serviceLabel() + " was not installed", nil
	}
	return "removed " + path, nil
}
