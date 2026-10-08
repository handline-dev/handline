package lifecycle

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/team"
)

type fakeDriver struct{ vendor string }

func (d fakeDriver) Name() string                     { return d.vendor }
func (fakeDriver) IsResume(extra []string) bool       { return len(extra) > 0 && extra[0] == "--resume" }
func (fakeDriver) Binary() string                     { return "/bin/vendor" }
func (fakeDriver) Hooks() Hooks                       { return nil }
func (fakeDriver) AutoModeArgv() []string             { return []string{"--permission-mode", "auto"} }
func (fakeDriver) PermissionModeArgv(string) []string { return nil }
func (fakeDriver) SkipPermissionsArgv() []string      { return []string{"--skip"} }
func (fakeDriver) LaunchArgv(exe string, o LaunchArgs) []string {
	return []string{exe, "--effort", o.Effort, "--disallowed", o.Disallowed}
}
func (fakeDriver) LaunchEnv(string, string, string) map[string]string { return nil }
func (fakeDriver) PromptArgv(p string) []string                       { return []string{"--", p} }
func (fakeDriver) TranscriptDir(string, string) (string, bool)        { return "", false }

type fakeVendors struct{ account string }

func (fakeVendors) ForAgent(string) (Driver, error) { return fakeDriver{"claude"}, nil }
func (fakeVendors) AgentVendor(string) string       { return "claude" }
func (v fakeVendors) AccountVendor(string) string   { return v.account }
func (fakeVendors) ConfigDir(string) string         { return "" }

type fakeUsage struct{ st AccountStatus }

func (u fakeUsage) Read(string) AccountStatus         { return u.st }
func (fakeUsage) ShortBlock() int                     { return 95 }
func (fakeUsage) LongBlock() int                      { return 98 }
func (fakeUsage) PickAccount([]string, string) string { return "" }
func (fakeUsage) NotFor(string, string) bool          { return false }
func (fakeUsage) Fit(string, string) bool             { return true }

type fakeAccounts struct{ loggedIn bool }

func (a fakeAccounts) MayStart(string) bool                   { return a.loggedIn }
func (fakeAccounts) LoginCommand(acct string) (string, error) { return "login " + acct, nil }
func (fakeAccounts) EnvFor(acct, agent string) map[string]string {
	return map[string]string{"OCP_ACCOUNT": acct, "OCP_AGENT": agent}
}

type launchRig struct {
	l     *Launcher
	warns []string
	execd []string
}

func newLaunchRig(t *testing.T) *launchRig {
	t.Helper()
	home := t.TempDir()
	t.Setenv("OCP_HOME", home)
	t.Setenv("HANDLINE_HOME", home)
	r := &launchRig{}
	roster := team.NewRoster(home)
	if err := os.MkdirAll(filepath.Dir(roster.AgentFile("ann")), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(roster.AgentFile("ann"), []byte("vendor: claude\nmodel: m1\neffort: high\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	r.l = &Launcher{Q: openDB(t), Home: home, Roster: roster, Ports: LaunchPorts{
		Usage:          fakeUsage{AccountStatus{Exists: true}},
		Accounts:       fakeAccounts{true},
		Vendors:        fakeVendors{"claude"},
		CurrentAccount: func(db.Q, string) string { return "" },
		TeamRules:      func(string) []string { return nil },
		Routing:        func() *team.Team { return nil },
		TerminalHandle: func() string { return "term_1" },
		WorktreeID:     func() string { return "wt_1" },
		RegisterWorker: func(string, string) error { return nil },
		Warn:           func(s string) { r.warns = append(r.warns, s) },
		Exec: func(argv0 string, argv, env []string) error {
			r.execd = append([]string{argv0}, env...)
			return nil
		},
	}}
	return r
}

func TestBuildRefusesWhatMustNotGoAhead(t *testing.T) {
	r := newLaunchRig(t)
	cases := []struct {
		name string
		set  func()
		want string
	}{
		{"account of another vendor", func() { r.l.Ports.Vendors = fakeVendors{"codex"} }, "is a codex account - refusing to start it there"},
		{"account not logged in", func() { r.l.Ports.Accounts = fakeAccounts{false} }, "is not logged in.\nA human has to do this once"},
		{"short window at the wall", func() {
			pct := 96
			r.l.Ports.Usage = fakeUsage{AccountStatus{Exists: true, ShortTrusted: &pct, ShortLabel: "5h", ShortReset: 1700000000}}
		}, "is at 96% of the 5h limit (>= 95%) - refusing to start.\nresets at 1700000000; use --force to override"},
	}
	for _, c := range cases {
		r.l.Ports.Vendors, r.l.Ports.Accounts = fakeVendors{"claude"}, fakeAccounts{true}
		r.l.Ports.Usage = fakeUsage{AccountStatus{Exists: true}}
		c.set()
		_, _, err := r.l.Build("ann", nil, false, "main")
		ref, ok := err.(*Refusal)
		if !ok || !strings.Contains(ref.Msg, c.want) {
			t.Errorf("%s: %v", c.name, err)
		}
	}
	// --force passes the wall.
	pct := 96
	r.l.Ports.Usage = fakeUsage{AccountStatus{Exists: true, ShortTrusted: &pct}}
	if _, _, err := r.l.Build("ann", nil, true, "main"); err != nil {
		t.Fatalf("force: %v", err)
	}
}

func TestBuildPutsTheModeAfterTheVendorsLineAndTheCallersArgsLast(t *testing.T) {
	r := newLaunchRig(t)
	r.l.Ports.CurrentAccount = func(db.Q, string) string { return "main" } // a resume goes back to its recorded account
	argv, env, err := r.l.Build("ann", []string{"--resume", "s1"}, false, "")
	if err != nil {
		t.Fatal(err)
	}
	if got := strings.Join(argv, " "); !strings.HasPrefix(got, "/bin/vendor --effort high --disallowed "+DisallowedTools+" --permission-mode auto") || !strings.HasSuffix(got, "--resume s1") {
		t.Fatalf("%s", got)
	}
	if env["OCP_ACCOUNT"] != "main" {
		t.Fatalf("%v", env)
	}
	// A mode of the caller's own wins and is not doubled.
	argv, _, _ = r.l.Build("ann", []string{"--permission-mode=plan"}, false, "main")
	if strings.Count(strings.Join(argv, " "), "permission-mode") != 1 {
		t.Fatalf("%v", argv)
	}
}

func TestExecAgentKeepsHowItStartedAndWhereItRunsThenExecs(t *testing.T) {
	r := newLaunchRig(t)
	if err := db.SetHandoff(r.l.Q, "ann", "left off here"); err != nil {
		t.Fatal(err)
	}
	if err := r.l.ExecAgent("ann", nil, false, "", "main", "wake", "orcapipe"); err != nil {
		t.Fatal(err)
	}
	if len(r.execd) == 0 || r.execd[0] != "/bin/vendor" {
		t.Fatalf("not exec'd: %v", r.execd)
	}
	if h, _ := db.GetHandoff(r.l.Q, "ann"); h != "" {
		t.Fatalf("a launch nobody waited on leaves no handoff behind: %q", h)
	}
	row, err := db.GetAgentState(r.l.Q, "ann")
	if err != nil || row == nil {
		t.Fatalf("%v %v", row, err)
	}
	if row.Terminal.String != "term_1" || row.Worktree.String != "wt_1" || row.Account.String != "main" || row.LaunchFingerprint.String == "" {
		t.Fatalf("%+v", row)
	}
	// With a prompt file the handoff is left for the daemon, and the file is taken.
	_ = db.SetHandoff(r.l.Q, "ann", "again")
	pf := filepath.Join(t.TempDir(), "p")
	_ = os.WriteFile(pf, []byte("hello"), 0o644)
	if err := r.l.ExecAgent("ann", nil, false, pf, "main", "", ""); err != nil {
		t.Fatal(err)
	}
	if h, _ := db.GetHandoff(r.l.Q, "ann"); h != "again" {
		t.Fatalf("%q", h)
	}
	if _, err := os.Stat(pf); err == nil {
		t.Fatal("prompt file kept")
	}
}

func TestARefusedLaunchLeavesThePromptFileAndExecsNothing(t *testing.T) {
	r := newLaunchRig(t)
	r.l.Ports.Accounts = fakeAccounts{false}
	pf := filepath.Join(t.TempDir(), "p")
	_ = os.WriteFile(pf, []byte("hello"), 0o644)
	if err := r.l.ExecAgent("ann", nil, false, pf, "main", "", ""); err == nil {
		t.Fatal("expected refusal")
	}
	if _, err := os.Stat(pf); err != nil || r.execd != nil {
		t.Fatalf("%v %v", err, r.execd)
	}
}
