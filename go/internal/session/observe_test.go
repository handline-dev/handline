package session

import (
	"errors"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

// The expected answers were read off the Python observe.is_session for the same commands.
func TestIsSession(t *testing.T) {
	cases := []struct {
		cmd  string
		want bool
	}{
		{"claude --foo", true}, {"/usr/local/bin/claude", true}, {"/opt/x/Library/Application Support/Claude/claude --x", true},
		{"claude -p hello", false}, {"claude --model x -p", false}, {"claude --append-system-prompt 'mkdir -p x' --x", true},
		{"claude --append-system-prompt 'a' -p b", true}, {"node claude", false}, {"/tmp/claude-501/script.sh", false},
		{"claude", true}, {"claudex", false}, {"opencode", true}, {"opencode --x", true}, {"/opt/bin/opencode -c", true},
		{"opencodex", false}, {"bash opencode", false}, {"claude --settings x -p y", true}, {"claude -p", false}, {"claude  -p  x", false},
	}
	for _, c := range cases {
		if got := IsSession(c.cmd); got != c.want {
			t.Errorf("IsSession(%q) = %v, want %v", c.cmd, got, c.want)
		}
	}
}

// psWorld answers the ps calls from a table of processes.
type psWorld struct {
	table   string
	tableOK bool
	argv    map[int]string // `ps ww`
	env     map[int]string // `ps eww`; absent: the same as argv (withheld)
	uid     map[int]int
	calls   []string
	slept   int
	failAll error
}

func (w *psWorld) run(timeout time.Duration, argv ...string) (Ran, error) {
	key := strings.Join(argv, " ")
	w.calls = append(w.calls, key)
	if w.failAll != nil {
		return Ran{}, w.failAll
	}
	switch {
	case argv[1] == "-Ao":
		if !w.tableOK {
			return Ran{Stderr: "ps: bad things happened\nsecond line", Code: 1}, nil
		}
		return Ran{Stdout: w.table}, nil
	case argv[1] == "-o" && argv[2] == "uid=":
		pid, _ := strconv.Atoi(argv[len(argv)-1])
		if u, ok := w.uid[pid]; ok {
			return Ran{Stdout: strconv.Itoa(u) + "\n"}, nil
		}
		return Ran{Code: 1}, nil
	default:
		pid, _ := strconv.Atoi(argv[len(argv)-1])
		a, ok := w.argv[pid]
		if !ok {
			return Ran{}, nil // gone: nothing on stdout, rc 0
		}
		if argv[1] == "eww" {
			if e, ok := w.env[pid]; ok {
				return Ran{Stdout: a + " " + e + "\n"}, nil
			}
		}
		return Ran{Stdout: a + "\n"}, nil
	}
}

func (w *psWorld) observer(reports *[]string) *Observer {
	return &Observer{Run: w.run, Sleep: func(time.Duration) { w.slept++ }, UID: 501,
		Report:         func(s string) { *reports = append(*reports, s) },
		AccountNamedBy: func(v string) string { return strings.TrimPrefix(v, "/accounts/") }}
}

func world() *psWorld {
	return &psWorld{tableOK: true,
		table: "  101 claude --model x\n  102 /bin/zsh -l\n  103 claude -p summarize this\n  104 opencode --run\n  105 claude\nnot a row\n  x notanumber\n",
		argv:  map[int]string{101: "claude --model x", 103: "claude -p summarize this", 104: "opencode --run", 105: "claude"},
		env: map[int]string{101: "PATH=/bin OCP_AGENT=worker.one CLAUDE_CONFIG_DIR=/accounts/alpha", 104: "HANDLINE_AGENT=oc OCP_AGENT=ignored XDG_DATA_HOME=/accounts/beta",
			105: "OCP_AGENT=worker.one"},
		uid: map[int]int{}}
}

func TestLivePIDsReadsTheAgentNameOutOfEachSessionsEnvironment(t *testing.T) {
	var reports []string
	o := world().observer(&reports)
	got, ok := o.LivePIDs()
	if !ok || len(got) != 2 || got["worker.one"] != 101 || got["oc"] != 104 {
		t.Fatalf("got %v %v", got, ok)
	}
	if len(reports) != 0 {
		t.Errorf("reported %v", reports)
	}
	sessions, ok := o.LiveSessions()
	if !ok || !sessions["oc"] || !sessions["worker.one"] || len(sessions) != 2 {
		t.Errorf("LiveSessions %v", sessions)
	}
}

func TestAnEmptyPrimaryNameFallsBackToTheOther(t *testing.T) {
	w := world()
	w.env[101] = "HANDLINE_AGENT= OCP_AGENT=late"
	var reports []string
	got, _ := w.observer(&reports).LivePIDs()
	if got["late"] != 101 {
		t.Errorf("got %v", got)
	}
}

func TestATableThatCannotBeReadIsNotAnEmptyMachine(t *testing.T) {
	w := world()
	w.tableOK = false
	var reports []string
	o := w.observer(&reports)
	if got, ok := o.LivePIDs(); ok || got != nil {
		t.Fatalf("got %v %v", got, ok)
	}
	if _, ok := o.LiveSessions(); ok {
		t.Fatal("LiveSessions ok on a table that could not be read")
	}
	if len(reports) != 1 || reports[0] != "process table could not be read: ps -Ao exited 1: ps: bad things happened" {
		t.Errorf("reports %q", reports)
	}
	w.tableOK = true
	o.LivePIDs()
	if len(reports) != 2 || reports[1] != "process table readable again" {
		t.Errorf("reports %q", reports)
	}
	o.LivePIDs()
	if len(reports) != 2 {
		t.Errorf("a steady state was reported again: %q", reports)
	}
}

func TestACauseIsNamedWhenPsCannotRun(t *testing.T) {
	var reports []string
	w := world()
	w.failAll = timedOut{time.Second}
	o := w.observer(&reports)
	o.LivePIDs()
	w.failAll = syscall.ENOENT
	o.LivePIDs()
	w.failAll = errors.New("boom")
	o.LivePIDs()
	if len(reports) != 1 || reports[0] != "process table could not be read: ps -Ao timed out after 20s" {
		t.Errorf("reports %q", reports)
	}
	tr := &trail{}
	w.failAll = syscall.ENOENT
	o.processTable(tr)
	if tr.why != "ps -Ao could not run: FileNotFoundError 2" {
		t.Errorf("why %q", tr.why)
	}
}

func TestAnotherUsersProcessIsLeftOutButOneOfOursWithNoEnvironmentFailsTheReading(t *testing.T) {
	w := world()
	delete(w.env, 101) // ps withholds its environment
	w.uid[101] = 0     // somebody else's
	var reports []string
	got, ok := w.observer(&reports).LivePIDs()
	if !ok || got["worker.one"] != 105 || len(got) != 2 {
		t.Fatalf("another user's process: %v %v", got, ok)
	}
	if w.slept != 0 {
		t.Error("looked twice at another user's process")
	}

	w = world()
	delete(w.env, 101)
	w.uid[101] = 501 // ours, and still withheld on the second look
	got, ok = w.observer(&reports).LivePIDs()
	if !ok || w.slept != 1 || len(got) != 2 {
		t.Fatalf("withheld twice is left out like another user's: %v %v slept %d", got, ok, w.slept)
	}

	w = world()
	delete(w.argv, 101) // in the table, gone by the time ps ww looks
	_, ok = w.observer(&reports).LivePIDs()
	if ok {
		t.Fatal("a session the table named and ps could not find did not fail the reading")
	}
	want := "process table could not be read: pid 101, one of 3 session processes of 5: its environment could not be read (ps eww gave nothing: the process is gone)"
	if got := reports[len(reports)-1]; got != want {
		t.Errorf("report %q", got)
	}
}

func TestProcessEnvIsNeverArgv(t *testing.T) {
	w := world()
	w.argv[101] = "claude --append-system-prompt OCP_AGENT=impostor"
	w.env[101] = "OCP_AGENT=real"
	var reports []string
	o := w.observer(&reports)
	env, ok := o.ProcessEnv(101)
	if !ok || env != " OCP_AGENT=real\n" {
		t.Errorf("env %q %v", env, ok)
	}
	if v, ok := o.EnvOf(101, "OCP_AGENT"); !ok || v != "real" {
		t.Errorf("EnvOf %q %v", v, ok)
	}
	if _, ok := o.EnvOf(101, "NOT_SET"); ok {
		t.Error("an unset variable was found")
	}
	if acct := o.AccountOfProcess(105); acct != "" {
		t.Errorf("a process with no config dir of ours named %q", acct)
	}
	w.env[101] = "CLAUDE_CONFIG_DIR=/accounts/alpha"
	if acct := o.AccountOfProcess(101); acct != "alpha" {
		t.Errorf("account %q", acct)
	}
	w.env[104] = "XDG_DATA_HOME=/accounts/beta"
	if acct := o.AccountOfProcess(104); acct != "beta" {
		t.Errorf("opencode's account %q", acct)
	}
}

func TestDiagnoseSaysHowEachSessionWasRead(t *testing.T) {
	w := world()
	delete(w.env, 105)
	w.uid[105] = 0
	var reports []string
	got := w.observer(&reports).Diagnose()
	want := []string{"pid 101: OCP_AGENT=worker.one", "pid 104: OCP_AGENT=ignored", "pid 105: another user's process"}
	if strings.Join(got, "\n") != strings.Join(want, "\n") {
		t.Errorf("got %q", got)
	}
	w.tableOK = false
	if got := w.observer(&reports).Diagnose(); len(got) != 1 || got[0] != "the process table could not be read" {
		t.Errorf("got %q", got)
	}
	w = world()
	w.table = "1 zsh\n"
	if got := w.observer(&reports).Diagnose(); len(got) != 1 || got[0] != "no process looks like a session" {
		t.Errorf("got %q", got)
	}
}

func TestObserveKeepsCouldNotLookApartFromNo(t *testing.T) {
	f := newFixture(t)
	f.term.live = map[string]bool{"h1": true}
	var reports []string
	w := world()
	o := w.observer(&reports)
	o.Term = f.term
	got := o.Observe([]string{"worker.one", "gone", "unterminalled"}, map[string]string{"worker.one": "h1", "gone": "h2"})
	if a := got["worker.one"]; a.TerminalAlive == nil || !*a.TerminalAlive || a.SessionPID == nil || *a.SessionPID != 101 || !a.SessionSeen {
		t.Errorf("worker.one %+v", a)
	}
	if a := got["gone"]; a.TerminalAlive == nil || *a.TerminalAlive || a.SessionPID != nil {
		t.Errorf("gone %+v", a)
	}
	if a := got["unterminalled"]; a.TerminalAlive != nil {
		t.Errorf("an agent with no handle has an answer: %+v", a)
	}
	f.term.down = true
	w.tableOK = false
	got = o.Observe([]string{"worker.one"}, map[string]string{"worker.one": "h1"})
	if a := got["worker.one"]; a.TerminalAlive != nil || a.SessionPID != nil || a.SessionSeen {
		t.Errorf("nothing could be looked at and something was said: %+v", a)
	}
}
