package session

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/handline/handline/go/internal/textsafe"
)

// What is actually true about the agents right now. Everything else in this system reads a row we wrote ourselves. That is how a
// state that went wrong stayed wrong: UserPromptSubmit set an agent RUNNING, nothing ever set it back, and for two and a half hours
// every screen faithfully reported a value nobody could contradict. The cure is not more hooks (an edge that is missed is missed
// forever) but asking the world again on a schedule.
//
// Two questions are answerable from outside, and both are answered for every agent at once, so the cost does not grow with the
// team: does its terminal still exist (one terminal listing), and is a session running in it (one pass over the process table).

// Observed is one agent as the outside world reports it. A nil pointer means we could not look, which is not the same as no: acting
// on "I could not ask the terminal host" as though it were "the terminal is gone" would tear down a healthy team the first time it
// was slow to start.
type Observed struct {
	TerminalAlive *bool
	SessionPID    *int
	SessionSeen   bool // whether the process table could be read
}

var (
	claudeSession   = regexp.MustCompile(`^(?:/[^\n]*?/)?claude(?:\s|$)`)
	claudeFreeText  = regexp.MustCompile(` --(?:append-system-prompt|settings)\b`)
	opencodeSession = regexp.MustCompile(`^(?:\S*/)?opencode(?:\s+-|$)`)
)

// IsSession is whether this process is an agent's session, rather than something else of ours: a claude run that is not headless,
// or opencode. Only the options up to the first one that carries free text are looked at: a session's --append-system-prompt is its
// whole preamble, handoff note included, and a " -p " in that ("mkdir -p", "git log -p") made a live session read as a headless run
// and vanish from the table. A headless run's -p comes before its prompt.
func IsSession(command string) bool {
	if claudeSession.MatchString(command) {
		head := command
		if loc := claudeFreeText.FindStringIndex(command); loc != nil {
			head = command[:loc[0]]
		}
		if !strings.Contains(head, " -p ") && !strings.HasSuffix(head, " -p") {
			return true
		}
	}
	return opencodeSession.MatchString(command)
}

// ConfigDirEnvs is the variable each known vendor's session keeps its credentials folder in (vendors' config_dir_env).
var ConfigDirEnvs = []string{"CLAUDE_CONFIG_DIR", "XDG_DATA_HOME"}

// EnvRetry is how long a live process of ours that ps gave no environment is left before a second look: ps does that now and then
// (one being replaced, or ps raced by it).
const EnvRetry = 300 * time.Millisecond

// Ran is what a finished command said.
type Ran struct {
	Stdout, Stderr string
	Code           int
}

// RunFunc runs argv for at most timeout; err is non-nil when it could not run or timed out.
type RunFunc func(timeout time.Duration, argv ...string) (Ran, error)

type timedOut struct{ d time.Duration }

func (t timedOut) Error() string { return "timed out after " + t.d.String() }

func runPS(timeout time.Duration, argv ...string) (Ran, error) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	var out, errb bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &errb
	err := cmd.Run()
	if ctx.Err() == context.DeadlineExceeded {
		return Ran{}, timedOut{timeout}
	}
	var exit *exec.ExitError
	if errors.As(err, &exit) {
		return Ran{out.String(), errb.String(), exit.ExitCode()}, nil
	}
	if err != nil {
		return Ran{}, err
	}
	return Ran{Stdout: out.String(), Stderr: errb.String()}, nil
}

// Observer reads the process table and the terminal host.
type Observer struct {
	Term Terminal
	// Report is the daemon's log: told once when readings begin to fail and once when they recover.
	Report func(string)
	Run    RunFunc
	Sleep  func(time.Duration)
	UID    int
	// AccountNamedBy is the account a config-dir variable's value points into, "" for a directory that is not one of ours.
	AccountNamedBy func(value string) string

	mu      sync.Mutex
	failing bool
}

// NewObserver is an Observer on the real machine.
func NewObserver(t Terminal, report func(string), accountNamedBy func(string) string) *Observer {
	return &Observer{Term: t, Report: report, Run: runPS, Sleep: time.Sleep, UID: os.Getuid(), AccountNamedBy: accountNamedBy}
}

// trail is why the last reading came back nil: which call, how it failed, the first line of its stderr. Never an environment value:
// the output of `ps eww` is not read into it, only its exit status and error line.
type trail struct{ why string }

func (t *trail) miss(why string) { t.why = why }

func (t *trail) cause() string {
	if t.why == "" {
		return "no cause noted"
	}
	return t.why
}

func psFailed(call string, r Ran) string {
	first := ""
	if said := strings.TrimSpace(r.Stderr); said != "" {
		first = textsafe.Oneline(strings.SplitN(said, "\n", 2)[0])
		if rs := []rune(first); len(rs) > 120 {
			first = string(rs[:120])
		}
	}
	s := fmt.Sprintf("%s exited %d", call, r.Code)
	if first != "" {
		s += ": " + first
	}
	return s
}

func osErrorName(err error) string {
	var errno syscall.Errno
	if errors.As(err, &errno) {
		switch errno {
		case syscall.ENOENT:
			return "FileNotFoundError " + strconv.Itoa(int(errno))
		case syscall.EACCES, syscall.EPERM:
			return "PermissionError " + strconv.Itoa(int(errno))
		}
		return "OSError " + strconv.Itoa(int(errno))
	}
	return "OSError"
}

// ps is `ps` with argv, or false with the cause noted when it could not run or timed out.
func (o *Observer) ps(tr *trail, timeout time.Duration, argv ...string) (Ran, bool) {
	call := strings.Join(argv[:min(2, len(argv))], " ")
	r, err := o.Run(timeout, argv...)
	if err == nil {
		return r, true
	}
	var to timedOut
	if errors.As(err, &to) {
		tr.miss(fmt.Sprintf("%s timed out after %ds", call, int(timeout.Seconds())))
	} else {
		tr.miss(call + " could not run: " + osErrorName(err))
	}
	return Ran{}, false
}

// ProcessTable is (pid, command) for everything running, or false if we could not look. Not an empty machine: a caller that treats
// the two alike concludes every session is gone the one time ps fails.
func (o *Observer) processTable(tr *trail) ([]proc, bool) {
	r, ok := o.ps(tr, 20*time.Second, "ps", "-Ao", "pid=,command=")
	if !ok {
		return nil, false
	}
	if r.Code != 0 {
		tr.miss(psFailed("ps -Ao", r))
		return nil, false
	}
	var rows []proc
	for _, line := range strings.Split(r.Stdout, "\n") {
		line = strings.TrimLeft(line, " \t\r\f\v")
		i := strings.IndexAny(line, " \t\r\f\v")
		if i < 0 {
			continue
		}
		pid, err := strconv.Atoi(line[:i])
		if err != nil {
			continue
		}
		rows = append(rows, proc{pid, strings.TrimLeft(line[i:], " \t\r\f\v")})
	}
	return rows, true
}

type proc struct {
	pid     int
	command string
}

// ProcessTable is the process table, nil when it could not be read.
func (o *Observer) ProcessTable() []proc {
	rows, _ := o.processTable(&trail{})
	return rows
}

func (o *Observer) psCommand(tr *trail, pid int, flags string) (string, bool) {
	r, ok := o.ps(tr, 10*time.Second, "ps", flags, "-o", "command=", "-p", strconv.Itoa(pid))
	if !ok {
		return "", false
	}
	if r.Code != 0 {
		tr.miss(psFailed("ps "+flags, r))
		return "", false
	}
	if strings.TrimSpace(r.Stdout) == "" {
		tr.miss("ps " + flags + " gave nothing: the process is gone")
		return "", false
	}
	return r.Stdout, true
}

func (o *Observer) ownerUID(tr *trail, pid int) (int, bool) {
	r, ok := o.ps(tr, 10*time.Second, "ps", "-o", "uid=", "-p", strconv.Itoa(pid))
	if !ok {
		return 0, false
	}
	if r.Code != 0 {
		tr.miss(psFailed("ps -o uid", r))
		return 0, false
	}
	uid, err := strconv.Atoi(strings.TrimSpace(r.Stdout))
	if err != nil {
		tr.miss("ps -o uid gave no number")
		return 0, false
	}
	return uid, true
}

// envOrReason is ProcessEnv's answer, plus whether a miss there is definitely another user's process rather than one of the other
// misses that can happen to a live pid we should not give up on.
//
// An environment ps withholds is looked at again once if the process is ours. If it is still withheld the process is left out of
// the table, as another user's is: one process whose environment cannot be read must not make the whole reading unreadable, which
// fails every agent's delivery closed at once (it did). The agent that process is the session of is covered by the screen.
func (o *Observer) envOrReason(tr *trail, pid int) (env string, ok, notOurs bool) {
	argv, aok := o.psCommand(tr, pid, "ww")
	withEnv, eok := o.psCommand(tr, pid, "eww")
	if !aok || !eok {
		return "", false, false
	}
	if withEnv == argv {
		owner, ok := o.ownerUID(tr, pid)
		if !ok || owner != o.UID {
			return "", false, true
		}
		o.Sleep(EnvRetry)
		withEnv, eok = o.psCommand(tr, pid, "eww")
		if !eok || withEnv == argv {
			return "", false, true
		}
	}
	prefix := strings.TrimRight(argv, "\n")
	if !strings.HasPrefix(withEnv, prefix) {
		tr.miss("ps eww did not begin with ps ww's argv")
		return "", false, false
	}
	return withEnv[len(prefix):], true, false
}

// Process is one row of the process table.
type Process struct {
	PID     int
	Command string
}

// Table is the process table as exported rows; ok is false when it could not be read.
func (o *Observer) Table() ([]Process, bool) {
	rows, ok := o.processTable(&trail{})
	out := make([]Process, len(rows))
	for i, r := range rows {
		out[i] = Process{r.pid, r.command}
	}
	return out, ok
}

// ProcessEnv is pid's own environment, past its argv: never argv itself, which a caller controls and can put an env-looking token
// inside (a launch argument such as --append-system-prompt carrying literal text like OCP_AGENT=x). ps eww prints argv followed by
// the environment; ps ww prints argv alone. The two share the exact same argv rendering, so eww's output with that shared prefix
// cut off is the environment and nothing else. ok is false whenever no environment came back: the process is gone, it is another
// user's (ps prints their command at rc 0 but withholds their environment, so the two calls come back byte-identical), or the
// prefix does not match, which this refuses rather than guess at where argv ends. A miss is "I don't know", never "the variable was
// not set".
func (o *Observer) ProcessEnv(pid int) (string, bool) {
	env, ok, _ := o.envOrReason(&trail{}, pid)
	return env, ok
}

func extract(env, name string) (string, bool) {
	for _, tok := range strings.Fields(env) {
		if strings.HasPrefix(tok, name+"=") {
			return tok[len(name)+1:], true
		}
	}
	return "", false
}

// EnvOf is one environment variable of a running process, as it was started with. The variables `hl launch` sets are how a process
// says which agent and which installation it belongs to. Unreadable and unset are the same answer here: neither says the process
// is somebody else's.
func (o *Observer) EnvOf(pid int, name string) (string, bool) {
	env, ok := o.ProcessEnv(pid)
	if !ok {
		return "", false
	}
	return extract(env, name)
}

// AccountOfProcess is the account a running process is spending: the one its own config-dir variable names. It is what the
// process was started with and never changes, so it is the one answer that cannot be out of date. "" when unreadable, or a config
// dir that is not one of ours.
func (o *Observer) AccountOfProcess(pid int) string {
	for _, name := range ConfigDirEnvs {
		if cfg, ok := o.EnvOf(pid, name); ok && cfg != "" {
			if acct := o.AccountNamedBy(cfg); acct != "" {
				return acct
			}
		}
	}
	return ""
}

// LivePIDs is liveSessions, and the cause of a failed reading said once to Report (and its end, once).
func (o *Observer) LivePIDs() (map[string]int, bool) {
	tr := &trail{}
	found, ok := o.liveSessions(tr)
	o.mu.Lock()
	was := o.failing
	o.failing = !ok
	o.mu.Unlock()
	if o.Report != nil {
		if !ok && !was {
			o.Report("process table could not be read: " + tr.cause())
		} else if ok && was {
			o.Report("process table readable again")
		}
	}
	return found, ok
}

// liveSessions is agent -> pid for every agent session that is actually running, whatever vendor it runs in. Read from the
// environment `hl launch` set before exec'ing the vendor's binary, which is the same string the rest of the system keys on. A fold
// is excluded: it is a headless run that inherits whatever OCP_AGENT its caller had, and it is not the agent's session.
//
// Not ok the way processTable is: a pid the table just named as one of ours, whose own ps calls then missed, fails the whole
// reading rather than being silently left out, unless it is definitely another user's process, which is left out on its own.
func (o *Observer) liveSessions(tr *trail) (map[string]int, bool) {
	table, ok := o.processTable(tr)
	if !ok {
		return nil, false
	}
	found := map[string]int{}
	for _, p := range table {
		if !IsSession(p.command) {
			continue
		}
		env, ok, notOurs := o.envOrReason(tr, p.pid)
		if !ok {
			if notOurs {
				continue
			}
			sessions := 0
			for _, q := range table {
				if IsSession(q.command) {
					sessions++
				}
			}
			tr.miss(fmt.Sprintf("pid %d, one of %d session processes of %d: its environment could not be read (%s)", p.pid, sessions, len(table), tr.cause()))
			return nil, false
		}
		name, _ := extract(env, "HANDLINE_AGENT")
		if name == "" {
			name, _ = extract(env, "OCP_AGENT")
		}
		if name != "" {
			if _, seen := found[name]; !seen {
				found[name] = p.pid
			}
		}
	}
	return found, true
}

// Diagnose is one line per process the table takes for an agent's session, saying how it was read: whose it is named as, or why it
// could not be. For the person looking into a session the table did not list. Only the agent's name is read out of the
// environment, never the rest of it.
func (o *Observer) Diagnose() []string {
	tr := &trail{}
	table, ok := o.processTable(tr)
	if !ok {
		return []string{"the process table could not be read"}
	}
	var lines []string
	for _, p := range table {
		if !IsSession(p.command) {
			continue
		}
		env, ok, notOurs := o.envOrReason(tr, p.pid)
		switch {
		case !ok && notOurs:
			lines = append(lines, fmt.Sprintf("pid %d: another user's process", p.pid))
		case !ok:
			lines = append(lines, fmt.Sprintf("pid %d: its environment could not be read", p.pid))
		default:
			name, _ := extract(env, "OCP_AGENT")
			if name == "" {
				name = "unset"
			}
			shown := textsafe.Oneline(name)
			if rs := []rune(shown); len(rs) > 60 {
				shown = string(rs[:60])
			}
			lines = append(lines, fmt.Sprintf("pid %d: OCP_AGENT=%s", p.pid, shown))
		}
	}
	if len(lines) == 0 {
		return []string{"no process looks like a session"}
	}
	return lines
}

// Observe is one look at the world, for all the agents at once. terminals is each agent's recorded handle, "" for none.
func (o *Observer) Observe(agents []string, terminals map[string]string) map[string]Observed {
	var handles map[string]bool
	if o.Term.Available() {
		handles = o.Term.LiveTerminals()
	}
	sessions, seen := o.LivePIDs()
	out := map[string]Observed{}
	for _, a := range agents {
		ob := Observed{SessionSeen: seen}
		if h := terminals[a]; handles != nil && h != "" {
			alive := handles[h]
			ob.TerminalAlive = &alive
		}
		if seen {
			if pid, ok := sessions[a]; ok {
				ob.SessionPID = &pid
			}
		}
		out[a] = ob
	}
	return out
}

// LiveSessions is the agents with a running session; ok is false when the table could not be read.
func (o *Observer) LiveSessions() (map[string]bool, bool) {
	found, ok := o.LivePIDs()
	if !ok {
		return nil, false
	}
	out := make(map[string]bool, len(found))
	for a := range found {
		out[a] = true
	}
	return out, true
}
