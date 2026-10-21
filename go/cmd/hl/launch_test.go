package main

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/compose"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/team"
)

// launchRig is a founder's rig whose home runs tmux, with a.one a Claude agent on account alpha.
func launchRig(t *testing.T) *rig {
	r := hookRig(t, false)
	for p, text := range map[string]string{
		paths.ConfigFile(r.home):                                               "terminal: tmux\n",
		filepath.Join(paths.AgentDir(r.home, "a.one"), "agent.yaml"):           "name: a.one\nrepo: " + r.root + "\naccount: alpha\n",
		filepath.Join(r.home, "accounts", "alpha", ".claude", "settings.json"): "{}\n",
	} {
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return r
}

func TestLaunchRefusesWhatItDoesNotLaunch(t *testing.T) {
	r := launchRig(t)
	if err := os.WriteFile(filepath.Join(paths.AgentDir(r.home, "b.two"), "agent.yaml"), []byte("name: b.two\nvendor: opencode\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	use := launchUsage + "\n"
	for name, c := range map[string]struct {
		env       []string
		args      []string
		out, errs string
		code      int
	}{
		"under Orca":     {[]string{"HANDLINE_TERMINAL=orca"}, []string{"launch", "a.one"}, "", "hl launch: the terminal is orca; this hl launches only under tmux\n", 1},
		"another vendor": {nil, []string{"launch", "b.two"}, "", "hl launch: b.two runs vendor opencode, which this hl does not launch\n", 1},
		"no agent named": {nil, []string{"launch"}, "", use, 2},
		"a typo":         {nil, []string{"launch", "a.one", "--acount", "x"}, "", use, 2},
		"help":           {nil, []string{"launch", "--help"}, use, "", 0},
	} {
		out, errs, code := r.run(c.env, c.args...)
		if out != c.out || errs != c.errs || code != c.code {
			t.Errorf("%s: out %q err %q exit %d", name, out, errs, code)
		}
	}
	if _, errs, code := r.run(nil, "launch", "z.z"); code != 1 || !strings.HasPrefix(errs, "hl launch: agent 'z.z' is not registered (") {
		t.Errorf("no config: err %q exit %d", errs, code)
	}
}

func TestLaunchKeepsTheHooksCurrentAndRefusesALoggedOutAccount(t *testing.T) {
	r := launchRig(t)
	// What follows -- is claude's: here a resume, with no session on record to resume.
	if _, errs, code := r.run(nil, "launch", "a.one", "--why", "test", "--", "--resume", "abc"); code != 1 ||
		errs != "agent 'a.one' has no recorded session to resume\n" {
		t.Fatalf("resume: exit %d, err %q", code, errs)
	}
	out, errs, code := r.run(nil, "launch", "a.one", "--account", "alpha", "--why", "test")
	if code != 1 || out != "" || !strings.HasPrefix(errs, "account 'alpha' is not logged in.\nA human has to do this once") {
		t.Fatalf("exit %d, out %q, err %q", code, out, errs)
	}
	settings, err := os.ReadFile(filepath.Join(r.home, "shared", ".claude", "settings.json"))
	if err != nil || !strings.Contains(string(settings), " hook PreToolUse\"") || !strings.Contains(string(settings), "\"allowUnixSockets\"") {
		t.Fatalf("shared settings %s %v", settings, err)
	}
}

func TestOwnTerminalIsAPaneOnOurServerOnly(t *testing.T) {
	wd, _ := os.Getwd()
	real, _ := filepath.EvalSymlinks(wd)
	home := t.TempDir()
	t.Setenv("HANDLINE_HOME", home)
	t.Setenv("OCP_HOME", home)
	t.Setenv("HANDLINE_TMUX_SOCKET", "")
	own := paths.TmuxSocket(home)
	for _, c := range []struct {
		pane, server, sock, handle string
	}{
		{"%12", "/tmp/tmux-501/" + own + ",123,0", "", "%12"},
		{"%12", "/tmp/tmux-501/ocp,123,0", "", ""}, // a scratch home's hl in a pane of the live server
		{"%12", "/tmp/tmux-501/ocp,123,0", "ocp", ""},
		{"%12", "/tmp/tmux-501/default,123,0", "", ""},
		{"%12", "/tmp/tmux-501/suite,1,0", "suite", "%12"},
		{"12", "/tmp/tmux-501/ocp,1,0", "", ""},
		{"", "/tmp/tmux-501/ocp,1,0", "", ""},
	} {
		t.Setenv("TMUX_PANE", c.pane)
		t.Setenv("TMUX", c.server)
		t.Setenv("HANDLINE_TMUX_SOCKET", c.sock)
		handle, wt := tmuxOwnTerminal()
		if handle != c.handle || (handle != "" && wt != real) || (handle == "" && wt != "") {
			t.Errorf("%+v: %q %q", c, handle, wt)
		}
	}
}

func TestLaunchRecordsTheTeamTextTheDaemonComparesWith(t *testing.T) {
	r := launchRig(t)
	if err := os.WriteFile(filepath.Join(r.home, "team.yaml"), []byte("agents:\n  a.one: {}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	d, err := store.Open(paths.DBFile(r.home))
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	teamText := func(typed func(string) bool) string {
		l := launcher(r.home, d, team.NewRoster(r.home))
		l.FounderTyped = typed
		parts, err := l.FingerprintParts("a.one")
		if err != nil {
			t.Fatal(err)
		}
		return parts["team"]
	}
	daemonOn, daemonOff := teamText(compose.PeerFromYAML(r.home)), teamText(nil)
	if daemonOn == daemonOff {
		t.Fatal("founder-typed does not change the team text; the test shows nothing")
	}
	for name, c := range map[string]struct {
		hello map[string]any
		err   error
		want  string
	}{
		"on":          {map[string]any{"capabilities": map[string]any{"founder_typed": true}}, nil, daemonOn},
		"off":         {map[string]any{"capabilities": map[string]any{"founder_typed": false}}, nil, daemonOff},
		"not said":    {map[string]any{"capabilities": map[string]any{}}, nil, daemonOff},
		"daemon down": {nil, errors.New("down"), daemonOff},
	} {
		if got := teamText(founderTyped(r.home, c.hello, c.err)); got != c.want {
			t.Errorf("%s: the team text differs from the daemon's", name)
		}
	}
}
