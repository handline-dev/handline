package lifecycle

import (
	"database/sql"
	"github.com/handline/handline/go/internal/states"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/sessiontoken"
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

type fakeUsage struct {
	st      AccountStatus
	by      map[string]AccountStatus
	pick    string
	cap     int
	others  int
	exclude *string // the agent ActiveOn was asked to leave out
}

func (u fakeUsage) CapOf(string) int { return u.cap }
func (u fakeUsage) ActiveOn(_ db.Q, _, agent string) int {
	if u.exclude != nil {
		*u.exclude = agent
	}
	return u.others
}

func (u fakeUsage) Read(acct string) AccountStatus {
	if st, ok := u.by[acct]; ok {
		return st
	}
	return u.st
}
func (fakeUsage) ShortBlock() int                       { return 95 }
func (fakeUsage) LongBlock() int                        { return 98 }
func (u fakeUsage) PickAccount([]string, string) string { return u.pick }
func (fakeUsage) NotFor(string, string) bool            { return false }
func (u fakeUsage) Fit(acct, _ string) bool {
	st := u.Read(acct)
	return st.ShortTrusted == nil || *st.ShortTrusted < u.ShortBlock()
}

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
	t.Setenv("HOME", t.TempDir()) // the protected store lives under the user home
	t.Setenv("OCP_AGENT", "")
	t.Setenv("HANDLINE_AGENT", "")
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
	r.l = &Launcher{Q: openDB(t), Home: home, Roster: roster, Tokens: sessiontoken.New(home), Ports: LaunchPorts{
		Usage:          fakeUsage{st: AccountStatus{Exists: true}},
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
			r.l.Ports.Usage = fakeUsage{st: AccountStatus{Exists: true, ShortTrusted: &pct, ShortLabel: "5h", ShortReset: 1700000000}}
		}, "is at 96% of the 5h limit (>= 95%) - refusing to start.\nresets at 1700000000; use --force to override"},
	}
	for _, c := range cases {
		r.l.Ports.Vendors, r.l.Ports.Accounts = fakeVendors{"claude"}, fakeAccounts{true}
		r.l.Ports.Usage = fakeUsage{st: AccountStatus{Exists: true}}
		c.set()
		_, _, err := r.l.Build("ann", nil, false, "main")
		ref, ok := err.(*Refusal)
		if !ok || !strings.Contains(ref.Msg, c.want) {
			t.Errorf("%s: %v", c.name, err)
		}
	}
	// --force passes the wall.
	pct := 96
	r.l.Ports.Usage = fakeUsage{st: AccountStatus{Exists: true, ShortTrusted: &pct}}
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

func addAccounts(t *testing.T, r *launchRig, names ...string) {
	t.Helper()
	for _, n := range names {
		if err := os.MkdirAll(filepath.Join(paths.AccountsDir(r.l.Home), n), 0o755); err != nil {
			t.Fatal(err)
		}
	}
}

func TestRoomIsTheLoggedInAccountNothingSaysIsBad(t *testing.T) {
	r := newLaunchRig(t)
	if got := r.l.NoRoom("ann"); got != "no account is registered - `hl account add <name>` first" {
		t.Fatal(got)
	}
	addAccounts(t, r, "a", "b")
	r.l.Ports.Accounts = fakeAccounts{false}
	if got := r.l.NoRoom("ann"); !strings.Contains(got, "no account is logged in") || !strings.Contains(got, "login a") {
		t.Fatal(got)
	}
	r.l.Ports.Accounts = fakeAccounts{true}
	if got := r.l.FindRoom("ann"); got != "a" {
		t.Fatalf("first fit: %q", got)
	}
	r.l.Ports.Usage = fakeUsage{pick: "b"}
	if got := r.l.ChooseAccount("ann", false, ""); got != "b" {
		t.Fatalf("picked: %q", got)
	}
	if got := r.l.ChooseAccount("ann", false, "a"); got != "a" {
		t.Fatalf("a move's destination stands: %q", got)
	}
	r.l.Ports.CurrentAccount = func(db.Q, string) string { return "b" }
	if got := r.l.ChooseAccount("ann", true, "a"); got != "b" {
		t.Fatalf("a resume goes back: %q", got)
	}
}

func TestAWalledWakeNamesTheAccountThatFreesFirst(t *testing.T) {
	r := newLaunchRig(t)
	addAccounts(t, r, "a", "b")
	walled := func(pct int, reset int64) AccountStatus {
		return AccountStatus{Exists: true, ShortTrusted: &pct, ShortLabel: "5h", ShortReset: reset}
	}
	r.l.Ports.Usage = fakeUsage{by: map[string]AccountStatus{"a": walled(97, 2000), "b": walled(99, 1000)}}
	if r.l.FindRoom("ann") != "" {
		t.Fatal("a full account has no room")
	}
	row := &db.AgentState{Agent: "ann", ClosedAt: sql.NullInt64{Int64: 5, Valid: true}}
	w := r.l.WakeWait(r.l.Q, "ann", row)
	if w == nil || w.Account != "b" || w.Pct != 99 || w.Assumed {
		t.Fatalf("%+v", w)
	}
	if got := DescribeWakeWait(w); !strings.HasPrefix(got, "waiting for b's 5h window (99%, rolls over ") {
		t.Fatal(got)
	}
	if got := r.l.NoRoom("ann"); !strings.HasPrefix(got, "no account can take one more agent - waiting for b's") {
		t.Fatal(got)
	}
	if got := r.l.WakeWaitReason(r.l.Q, "ann", row); got != DescribeWakeWait(w) {
		t.Fatal(got)
	}
	// A resume is pinned to the account it is tied to, and a guess says so.
	row.ResumeSession = sql.NullString{String: "s1", Valid: true}
	r.l.Ports.AccountReading = func(db.Q, string) (string, string, bool) { return "a", "assumed", true }
	if w := r.l.WakeWait(r.l.Q, "ann", row); w == nil || w.Account != "a" || !w.Assumed ||
		!strings.Contains(DescribeWakeWait(w), "assumed account a") {
		t.Fatalf("%+v", w)
	}
	// Open and live agents are never walled; a longest-window wall reads as a date.
	if r.l.WakeWait(r.l.Q, "ann", &db.AgentState{}) != nil {
		t.Fatal("an open agent was walled")
	}
	if got := WaitClock(&WakeWait{}, "?"); got != "?" {
		t.Fatal(got)
	}
	if got := WaitClock(&WakeWait{Reset: 86400 * 3, IsLong: true}, "?"); len(got) != len("01-02 15:04") {
		t.Fatal(got)
	}
}

func TestCommandForNamesTheLaunchAndQuotesWhatIsGiven(t *testing.T) {
	t.Setenv("OCP_BIN", "/x/hl")
	if got := CommandFor("", "", "", "ann"); got != "/x/hl launch ann" {
		t.Fatal(got)
	}
	if got := CommandFor("acct 2", "wake up", "boss", "ann"); got != "/x/hl launch ann --account 'acct 2' --why 'wake up' --by boss" {
		t.Fatal(got)
	}
}

func TestDriftedIsAFingerprintThatDiffersFromTheRecordedOne(t *testing.T) {
	r := newLaunchRig(t)
	fp, err := r.l.Fingerprint("ann")
	if err != nil || fp == "" {
		t.Fatal(fp, err)
	}
	rec, _ := r.l.LaunchRecord("ann")
	row := &db.AgentState{LaunchFingerprint: sql.NullString{String: fp, Valid: true}, LaunchParts: sql.NullString{String: rec, Valid: true}}
	if r.l.Drifted("ann", row) {
		t.Fatal("drifted from itself")
	}
	row.LaunchFingerprint.String = "other"
	if !r.l.Drifted("ann", row) || !(LaunchPort{r.l}).Drifted("ann", row) {
		t.Fatal("a different recipe did not drift")
	}
	if LaunchedAtOf(row) == "" {
		t.Fatal("the launch stamp was not read back")
	}
	if r.l.Drifted("nobody", row) {
		t.Fatal("an unreadable recipe is not drift")
	}
}

func TestLaunchPortAnswersForTheLauncher(t *testing.T) {
	r := newLaunchRig(t)
	p := LaunchPort{r.l}
	if p.ChooseAccount(r.l.Q, "ann") {
		t.Fatal("no account registered")
	}
	addAccounts(t, r, "a")
	if !p.ChooseAccount(r.l.Q, "ann") || p.NoRoom("ann") == "" {
		t.Fatal("room expected")
	}
	if cmd, err := p.CommandFor("ann", "a", "why", ""); err != nil || !strings.Contains(cmd, "launch ann --account a --why why") {
		t.Fatal(cmd, err)
	}
	if p.WakeWait(r.l.Q, "ann", &db.AgentState{}) != nil {
		t.Fatal("walled")
	}
	if p.DescribeWakeWait(&WakeWait{Account: "a", Window: "5h", Pct: 99}) == "" {
		t.Fatal("empty")
	}
}

func TestALaunchWithoutTheTokenStoreIsRefusedNotRunBare(t *testing.T) {
	r := newLaunchRig(t)
	r.l.Tokens = nil
	if _, _, err := r.l.Build("ann", nil, false, "main"); err == nil || !strings.Contains(err.Error(), "token store") {
		t.Fatalf("%v", err)
	}
	if err := r.l.ExecAgent("ann", nil, false, "", "main", "", ""); err == nil || r.execd != nil {
		t.Fatalf("%v %v", err, r.execd)
	}
}

func TestTheSessionIsGivenItsProofAndKeyAndTheKeyFolderIsHiddenFromIt(t *testing.T) {
	r := newLaunchRig(t)
	hide := ""
	r.l.Ports.Vendors = hidingVendors{&hide}
	if err := r.l.ExecAgent("ann", nil, false, "", "main", "", ""); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(hide, r.l.Tokens.KeyFolder()) || r.l.Tokens.KeyHeld("ann") == "" {
		t.Fatalf("hidden %q, key %q", hide, r.l.Tokens.KeyHeld("ann"))
	}
}

type hidingVendors struct{ hide *string }

func (v hidingVendors) ForAgent(string) (Driver, error) {
	return hidingDriver{fakeDriver{"claude"}, v.hide}, nil
}
func (hidingVendors) AgentVendor(string) string   { return "claude" }
func (hidingVendors) AccountVendor(string) string { return "claude" }
func (hidingVendors) ConfigDir(string) string     { return "" }

type hidingDriver struct {
	fakeDriver
	hide *string
}

func (d hidingDriver) LaunchArgv(exe string, o LaunchArgs) []string {
	*d.hide = strings.Join(o.Hide, "\n")
	return d.fakeDriver.LaunchArgv(exe, o)
}

func TestAResumeWaitsForASlotOnItsOwnAccountWhenTheOthersAndOlderWaitersFillIt(t *testing.T) {
	r := newLaunchRig(t)
	var left string
	r.l.Ports.AccountReading = func(db.Q, string) (string, string, bool) { return "a", "recorded", true }
	r.l.Ports.CurrentAccount = func(db.Q, string) string { return "a" }
	r.l.Ports.WaitingFor = func(_ db.Q, agent string) bool { return agent != "quiet" }
	q := r.l.Q
	db.SetAgentState(q, "ann", states.Idle, 0, "")
	db.SetClosed(q, "ann", "s-ann")
	older := func(agent, state string, failures int) {
		db.SetAgentState(q, agent, state, 0, "")
		db.SetClosed(q, agent, "s-"+agent)
		for i := 0; i < failures; i++ {
			db.BumpCounter(q, agent, "wake_failures", 1)
		}
		q.Exec("UPDATE agent_state SET closed_at = 1 WHERE agent = ?", agent)
	}
	q.Exec("UPDATE agent_state SET closed_at = 10 WHERE agent = 'ann'")
	row, _ := db.GetAgentState(q, "ann")
	wait := func(cap, others int) *WakeWait {
		r.l.Ports.Usage = fakeUsage{st: AccountStatus{Exists: true}, cap: cap, others: others, exclude: &left}
		return r.l.WakeWait(q, "ann", row)
	}
	if w := wait(2, 2); w == nil || w.Full != 2 || w.Busy != 2 || w.Older != 0 || w.Account != "a" {
		t.Fatalf("a full account (busy fill the cap): %+v", w)
	}
	if left != "ann" {
		t.Fatalf("the agent placed is left out of the count: %q", left)
	}
	if wait(2, 1) != nil {
		t.Fatal("a free slot is no wait")
	}
	older("bob", states.Idle, 0)
	older("quiet", states.Idle, 0)
	older("stuck", states.Stalled, 0)
	older("failing", states.Idle, states.MaxFailedAttempts)
	w := wait(2, 1)
	if w == nil || w.Full != 2 || w.Busy != 1 || w.Older != 1 {
		t.Fatalf("one older waiter takes the free slot (not stalled, not exhausted, not unwanted): %+v", w)
	}
	if got := DescribeWakeWait(w); got != "waiting for a slot on a (1 of 2 mid-turn, 1 older waiting go first)" {
		t.Fatal(got)
	}
	if got := DescribeWakeWait(&WakeWait{Account: "a", Full: 2, Busy: 2}); got != "waiting for a slot on a (2 of 2 mid-turn)" {
		t.Fatal(got)
	}
	if wait(3, 1) != nil {
		t.Fatal("room for the agent and the older waiter")
	}
}
