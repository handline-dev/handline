// Package devinstance runs a team's dev instance: a daemon and its gateway built from the team's own worktree, on a home of their own
// beside the live one (~/.handline-dev/<team>), seeded from a read-only snapshot of the live database.
//
// The daemon runs with -home only: no policy tick and no session ports, so it serves reads and writes on its own database and never
// starts, wakes, closes or tells an agent session. Its socket, protected store (derived from the home path), gateway port, logs and
// launchd labels are its own; nothing here writes the live home.
package devinstance

import (
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"github.com/handline/handline/go/internal/paths"
	_ "modernc.org/sqlite"
)

// Ports is each team's gateway port; a team not here has no dev instance.
var Ports = map[string]int{"handline.daemon": 7451, "handline.pc": 7452, "handline.mobile": 7453}

// Instance is what instance.json says: where the team's dev instance lives and what it runs.
type Instance struct {
	Team     string   `json:"team"`
	Home     string   `json:"home"`
	Worktree string   `json:"worktree"`
	Branch   string   `json:"branch"`
	Sha      string   `json:"sha"`
	Socket   string   `json:"socket"`
	Gateway  string   `json:"gateway"`
	Labels   []string `json:"labels"`
}

// Env is what the instance commands touch outside their own folder.
type Env struct {
	UserHome string // the person's home: instances live under .handline-dev, launchd plists under Library/LaunchAgents
	LiveHome string // the live handline home the snapshot is read from
	UID      int
	// Launchctl runs launchctl with args; the error carries its own words.
	Launchctl func(args ...string) (string, error)
	// Run runs name with args in dir and returns its output.
	Run func(dir, name string, args ...string) (string, error)
	// PortFree is whether addr can be listened on now.
	PortFree func(addr string) bool
	Path     string // PATH for the launchd jobs
}

// System is the Env of this machine: the person's home, launchctl, and processes and ports as they are.
func System(liveHome string) *Env {
	return &Env{
		UserHome: paths.UserHome(),
		LiveHome: liveHome,
		UID:      os.Getuid(),
		Launchctl: func(args ...string) (string, error) {
			out, err := exec.Command("launchctl", args...).CombinedOutput()
			return string(out), err
		},
		Run: func(dir, name string, args ...string) (string, error) {
			cmd := exec.Command(name, args...)
			cmd.Dir = dir
			out, err := cmd.CombinedOutput()
			return string(out), err
		},
		PortFree: func(addr string) bool {
			ln, err := net.Listen("tcp", addr)
			if err != nil {
				return false
			}
			ln.Close()
			return true
		},
		Path: os.Getenv("PATH"),
	}
}

func (e *Env) root() string            { return filepath.Join(e.UserHome, ".handline-dev") }
func (e *Env) home(team string) string { return filepath.Join(e.root(), team) }
func (e *Env) plist(label string) string {
	return filepath.Join(e.UserHome, "Library", "LaunchAgents", label+".plist")
}

// Labels are the launchd labels of a team's daemon and gateway.
func Labels(team string) []string {
	return []string{"dev.handline.dev." + team, "dev.handline.dev." + team + ".gateway"}
}

func check(team string) error {
	if _, ok := Ports[team]; !ok {
		names := make([]string, 0, len(Ports))
		for n := range Ports {
			names = append(names, n)
		}
		sort.Strings(names)
		return fmt.Errorf("%s has no dev instance: one of %s", team, strings.Join(names, ", "))
	}
	return nil
}

// resolved is p absolute with links followed, through its nearest ancestor that exists: a home not made yet still
// compares by where it will be.
func resolved(p string) string {
	if a, err := filepath.Abs(p); err == nil {
		p = a
	}
	for rest := ""; ; {
		if r, err := filepath.EvalSymlinks(p); err == nil {
			return filepath.Join(r, rest)
		}
		parent := filepath.Dir(p)
		if parent == p {
			return filepath.Join(p, rest)
		}
		rest, p = filepath.Join(filepath.Base(p), rest), parent
	}
}

// within is whether p is dir or inside it.
func within(p, dir string) bool {
	p, dir = resolved(p), resolved(dir)
	return p == dir || strings.HasPrefix(p, dir+string(filepath.Separator))
}

// OnOpts are `hl dev on`'s choices: the worktree to build (default: the team manager's), the gateway address (default 127.0.0.1:<port>),
// and whether to retake the snapshot over the one the folder keeps.
type OnOpts struct {
	Worktree, Listen string
	Fresh            bool
}

// Loaded is whether launchd has the label.
func (e *Env) Loaded(label string) bool {
	_, err := e.Launchctl("print", fmt.Sprintf("gui/%d/%s", e.UID, label))
	return err == nil
}

// On builds the team's daemon from its worktree, seeds the home, and starts the daemon and its gateway under launchd.
func (e *Env) On(team string, o OnOpts) (*Instance, error) {
	if err := check(team); err != nil {
		return nil, err
	}
	home := e.home(team)
	if within(home, e.LiveHome) || within(e.LiveHome, home) {
		return nil, fmt.Errorf("%s overlaps the live home %s", home, e.LiveHome)
	}
	labels := Labels(team)
	for _, l := range labels {
		if e.Loaded(l) {
			return nil, fmt.Errorf("%s's dev instance is already on (%s): `hl dev off %s` first, or `hl dev rebuild %s`", team, l, team, team)
		}
	}
	listen := o.Listen
	if listen == "" {
		listen = "127.0.0.1:" + strconv.Itoa(Ports[team])
	}
	if !e.PortFree(listen) {
		return nil, fmt.Errorf("%s is in use", listen)
	}
	for _, d := range []string{filepath.Join(home, "bin"), filepath.Join(home, "logs"), filepath.Join(home, "agents")} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			return nil, err
		}
	}
	if err := e.seed(home, o.Fresh); err != nil {
		return nil, err
	}
	wt := o.Worktree
	if wt == "" {
		var err error
		if wt, err = managerWorktree(filepath.Join(home, "db.sqlite"), team); err != nil {
			return nil, err
		}
	}
	in := &Instance{Team: team, Home: home, Worktree: wt, Socket: paths.SocketFile(home), Gateway: listen, Labels: labels}
	if err := e.build(in); err != nil {
		return nil, err
	}
	bin := filepath.Join(home, "bin", "handlined")
	args := [][]string{{bin, "-home", home}, {bin, "-gateway", "-home", home, "-listen", listen}}
	for i, l := range labels {
		name := []string{"daemon", "gateway"}[i]
		body := e.plistBody(l, args[i], home, filepath.Join(home, "logs", name+".out.log"), filepath.Join(home, "logs", name+".err.log"))
		if err := os.MkdirAll(filepath.Dir(e.plist(l)), 0o755); err != nil {
			return nil, err
		}
		if err := os.WriteFile(e.plist(l), []byte(body), 0o644); err != nil {
			return nil, err
		}
		if out, err := e.Launchctl("bootstrap", fmt.Sprintf("gui/%d", e.UID), e.plist(l)); err != nil {
			e.Off(team)
			return nil, fmt.Errorf("launchctl bootstrap %s: %v %s", l, err, strings.TrimSpace(out))
		}
	}
	return in, e.write(in)
}

// Off stops the team's daemon and gateway and removes their launchd jobs; the folder stays for the next On.
func (e *Env) Off(team string) error {
	if err := check(team); err != nil {
		return err
	}
	var errs []error
	for _, l := range Labels(team) {
		if e.Loaded(l) {
			if out, err := e.Launchctl("bootout", fmt.Sprintf("gui/%d/%s", e.UID, l)); err != nil {
				errs = append(errs, fmt.Errorf("launchctl bootout %s: %v %s", l, err, strings.TrimSpace(out)))
				continue
			}
		}
		if err := os.Remove(e.plist(l)); err != nil && !os.IsNotExist(err) {
			errs = append(errs, err)
		}
	}
	return errors.Join(errs...)
}

// Rebuild builds the binary again from the instance's worktree and restarts the daemon and gateway onto it.
func (e *Env) Rebuild(team string) (*Instance, error) {
	if err := check(team); err != nil {
		return nil, err
	}
	in, err := e.Read(team)
	if err != nil {
		return nil, err
	}
	if err := e.build(in); err != nil {
		return nil, err
	}
	for _, l := range in.Labels {
		if !e.Loaded(l) {
			continue
		}
		if out, err := e.Launchctl("kickstart", "-k", fmt.Sprintf("gui/%d/%s", e.UID, l)); err != nil {
			return nil, fmt.Errorf("launchctl kickstart %s: %v %s", l, err, strings.TrimSpace(out))
		}
	}
	return in, e.write(in)
}

// Status is one line per team: on or off, and for an instance that was ever on, its branch@sha, socket and gateway.
func (e *Env) Status() []string {
	teams := make([]string, 0, len(Ports))
	for t := range Ports {
		teams = append(teams, t)
	}
	sort.Strings(teams)
	var out []string
	for _, t := range teams {
		state := "off"
		if e.Loaded(Labels(t)[0]) {
			state = "on"
		}
		line := fmt.Sprintf("%-16s %s", t, state)
		if in, err := e.Read(t); err == nil {
			line += fmt.Sprintf("  %s@%s  %s  %s", in.Branch, in.Sha, in.Socket, in.Gateway)
		}
		out = append(out, line)
	}
	return out
}

// Read is the team's instance.json.
func (e *Env) Read(team string) (*Instance, error) {
	b, err := os.ReadFile(filepath.Join(e.home(team), "instance.json"))
	if err != nil {
		if os.IsNotExist(err) {
			return nil, fmt.Errorf("%s has no dev instance yet: `hl dev on %s`", team, team)
		}
		return nil, err
	}
	in := &Instance{}
	return in, json.Unmarshal(b, in)
}

func (e *Env) write(in *Instance) error {
	b, err := json.MarshalIndent(in, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(in.Home, "instance.json"), append(b, '\n'), 0o644)
}

// build stamps the worktree's branch and sha and builds handlined with the worktree's own build script.
func (e *Env) build(in *Instance) error {
	branch, err := e.Run(in.Worktree, "git", "rev-parse", "--abbrev-ref", "HEAD")
	if err != nil {
		return fmt.Errorf("%s is not a git worktree: %v", in.Worktree, err)
	}
	sha, err := e.Run(in.Worktree, "git", "rev-parse", "--short", "HEAD")
	if err != nil {
		return err
	}
	in.Branch, in.Sha = strings.TrimSpace(branch), strings.TrimSpace(sha)
	if out, err := e.Run(in.Worktree, filepath.Join(in.Worktree, "tools", "build-go.sh"), filepath.Join(in.Home, "bin", "handlined")); err != nil {
		return fmt.Errorf("build in %s: %v\n%s", in.Worktree, err, out)
	}
	return nil
}

// seed copies the live roster into home and, when home has no database yet or fresh is asked, a snapshot of the live database: VACUUM
// INTO over a read-only connection, so the live file is only read.
func (e *Env) seed(home string, fresh bool) error {
	for _, rel := range []string{"team.yaml"} {
		if err := copyIfThere(filepath.Join(e.LiveHome, rel), filepath.Join(home, rel)); err != nil {
			return err
		}
	}
	yamls, _ := filepath.Glob(filepath.Join(e.LiveHome, "agents", "*", "agent.yaml"))
	for _, y := range yamls {
		agent := filepath.Base(filepath.Dir(y))
		if err := os.MkdirAll(filepath.Join(home, "agents", agent), 0o755); err != nil {
			return err
		}
		if err := copyIfThere(y, filepath.Join(home, "agents", agent, "agent.yaml")); err != nil {
			return err
		}
	}
	dbFile := filepath.Join(home, "db.sqlite")
	if _, err := os.Stat(dbFile); err == nil && !fresh {
		return nil
	}
	for _, f := range []string{dbFile, dbFile + "-wal", dbFile + "-shm"} {
		if err := os.Remove(f); err != nil && !os.IsNotExist(err) {
			return err
		}
	}
	live, err := sql.Open("sqlite", "file:"+paths.DBFile(e.LiveHome)+"?mode=ro")
	if err != nil {
		return err
	}
	defer live.Close()
	if _, err := live.Exec("VACUUM INTO ?", dbFile); err != nil {
		return fmt.Errorf("snapshot of the live database: %v", err)
	}
	return nil
}

func copyIfThere(from, to string) error {
	b, err := os.ReadFile(from)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	return os.WriteFile(to, b, 0o644)
}

// managerWorktree is the worktree the snapshot records for the team's manager, the agent named like the team.
func managerWorktree(dbFile, team string) (string, error) {
	d, err := sql.Open("sqlite", "file:"+dbFile+"?mode=ro")
	if err != nil {
		return "", err
	}
	defer d.Close()
	var wt sql.NullString
	if err := d.QueryRow("SELECT worktree FROM agent_state WHERE agent = ?", team).Scan(&wt); err != nil && !errors.Is(err, sql.ErrNoRows) {
		return "", err
	}
	if wt.String == "" {
		return "", fmt.Errorf("%s has no worktree on record: pass --worktree PATH", team)
	}
	return wt.String, nil
}

func (e *Env) plistBody(label string, args []string, home, out, errLog string) string {
	s := func(v string) string { return "<string>" + html.EscapeString(v) + "</string>" }
	var b strings.Builder
	b.WriteString(`<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
`)
	b.WriteString("<key>Label</key>" + s(label) + "\n<key>ProgramArguments</key><array>")
	for _, a := range args {
		b.WriteString(s(a))
	}
	b.WriteString("</array>\n<key>RunAtLoad</key><true/>\n<key>KeepAlive</key><true/>\n<key>ThrottleInterval</key><integer>10</integer>\n")
	b.WriteString("<key>StandardOutPath</key>" + s(out) + "\n<key>StandardErrorPath</key>" + s(errLog) + "\n")
	b.WriteString("<key>EnvironmentVariables</key><dict><key>HOME</key>" + s(e.UserHome) + "<key>PATH</key>" + s(e.Path) +
		"<key>HANDLINE_HOME</key>" + s(home) + "</dict>\n</dict>\n</plist>\n")
	return b.String()
}
