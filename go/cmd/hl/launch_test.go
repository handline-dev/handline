package main

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/protected"
	"github.com/handline/handline/go/internal/team"
)

// launchRig is a founder's rig whose home runs tmux, with a.one a Claude agent on account alpha, the Go launch switched on.
func launchRig(t *testing.T) *rig {
	r := hookRig(t, false)
	if err := protected.New(r.home).Put("switches", "go_launch", "on"); err != nil {
		t.Fatal(err)
	}
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

func TestLaunchIsPythonsUnlessSwitchedOnForAClaudeAgentUnderTmux(t *testing.T) {
	off := hookRig(t, false)
	r := launchRig(t)
	if err := os.WriteFile(filepath.Join(paths.AgentDir(r.home, "b.two"), "agent.yaml"), []byte("name: b.two\nvendor: opencode\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	for name, c := range map[string]struct {
		r    *rig
		env  []string
		args []string
	}{
		"switched off":            {off, nil, []string{"launch", "a.one"}},
		"under Orca":              {r, []string{"HANDLINE_TERMINAL=orca"}, []string{"launch", "a.one"}},
		"another vendor":          {r, nil, []string{"launch", "b.two"}},
		"an agent with no config": {r, nil, []string{"launch", "z.z"}},
		"no agent named":          {r, nil, []string{"launch"}},
		"an option Python parses": {r, nil, []string{"launch", "a.one", "--acount", "x"}},
	} {
		out, _, code := c.r.run(c.env, c.args...)
		if code != 0 || !strings.HasPrefix(out, "python ") || !strings.HasSuffix(out, " "+strings.Join(c.args, " ")+"\n") {
			t.Errorf("%s: %q %d", name, out, code)
		}
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

func TestTeamRulesAreTheTeamsTheAgentIsInWithText(t *testing.T) {
	home := t.TempDir()
	dir := filepath.Join(home, "shared", "team")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	for name, text := range map[string]string{"zeta": "  last\n", "alpha": "\nfirst rule\n", "empty": " \n", "other": "not mine"} {
		if err := os.WriteFile(filepath.Join(dir, name+".md"), []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	tm := &team.Team{Teams: map[string][]string{"zeta": {"a"}, "alpha": {"b", "a"}, "empty": {"a"}, "other": {"b"}, "nofile": {"a"}}}
	want := []string{"# Rules for team alpha\nfirst rule", "# Rules for team zeta\nlast"}
	if got := teamRules(home, "a", tm); !reflect.DeepEqual(got, want) {
		t.Errorf("got %q", got)
	}
	if got := teamRules(home, "a", nil); got != nil {
		t.Errorf("no routing: %q", got)
	}
}

func TestOwnTerminalIsAPaneOnOurServerOnly(t *testing.T) {
	wd, _ := os.Getwd()
	real, _ := filepath.EvalSymlinks(wd)
	for _, c := range []struct {
		pane, server, sock, handle string
	}{
		{"%12", "/tmp/tmux-501/ocp,123,0", "", "%12"},
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
