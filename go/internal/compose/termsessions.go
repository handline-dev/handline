package compose

import (
	"context"
	"encoding/json"
	"errors"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/peerinbox"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/team"
)

// A running Claude Code session leaves <config dir>/sessions/<pid>.json (its working directory, session id and peer protocol). A terminal
// agent's session is the one of those whose directory is the agent's worktree, whose process is there and whose terminal is still listed:
// the process table is not read, and a file left by a session that died is not taken for one that runs.

// sessionRec is what a session file says: the process, the session, and what the session reports of itself (idle, busy, waiting with
// what it waits for, or at a shell).
type sessionRec struct {
	Pid        int
	SessionID  string
	Status     string
	WaitingFor string
	UpdatedAt  int64 // the record's own clock, in ms
}

// lookup is what looking for a session says. Unknown is not none: a file that cannot be read or parsed under a live pid may be the session
// being looked for, and taking it for no session would have a live agent recorded closed.
type lookup int

const (
	sessionNone lookup = iota
	sessionFound
	sessionUnknown
)

// terminalSessions is the terminal agents with a session running, agent to the session's pid.
func (p *Parts) terminalSessions() map[string]int {
	out := map[string]int{}
	if p.LC == nil {
		return out
	}
	live := p.LC.term.LiveTerminals()
	for _, agent := range paths.ListAgents(p.home) {
		if p.Kind(agent) {
			continue
		}
		if rec, _, found := p.terminalSession(agent, live); found == sessionFound {
			out[agent] = rec.Pid
		}
	}
	return out
}

// terminalSession is the running session of a terminal agent and the account whose config directory holds its file (the account it
// spends, whatever agent.yaml says). live is the terminals the host lists, nil when it could not say.
func (p *Parts) terminalSession(agent string, live map[string]bool) (sessionRec, string, lookup) {
	row, err := db.GetAgentState(p.db, agent)
	if err != nil || row == nil || row.Terminal.String == "" || (live != nil && !live[row.Terminal.String]) {
		return sessionRec{}, "", sessionNone
	}
	wt := lifecycle.WorktreePath(row.Worktree.String)
	if wt == "" {
		return sessionRec{}, "", sessionNone
	}
	accts := paths.ListAccounts(p.home)
	if row.Account.String != "" { // the recorded account first: a session found there is the expected one
		accts = append([]string{row.Account.String}, accts...)
	}
	result := sessionNone
	for _, acct := range accts {
		dir, err := team.AccountConfigDir(p.home, acct)
		if err != nil {
			continue
		}
		switch rec, found := sessionIn(filepath.Join(dir, "sessions"), wt); found {
		case sessionFound:
			return rec, acct, sessionFound
		case sessionUnknown:
			result = sessionUnknown
		}
	}
	return sessionRec{}, "", result
}

func alive(pid int) bool {
	err := syscall.Kill(pid, 0)
	return err == nil || err == syscall.EPERM
}

// sessionIn is the running session whose working directory is wt; of several the newest by updatedAt. A directory that cannot be listed, or a
// file under a live pid that cannot be read or parsed, makes the answer unknown unless a session is found beside it.
func sessionIn(dir, wt string) (sessionRec, lookup) {
	entries, err := os.ReadDir(dir)
	if errors.Is(err, fs.ErrNotExist) {
		return sessionRec{}, sessionNone
	}
	if err != nil {
		return sessionRec{}, sessionUnknown
	}
	best, unknown := sessionRec{}, false
	var bestAt int64
	for _, e := range entries {
		name := e.Name()
		pid, err := strconv.Atoi(strings.TrimSuffix(name, ".json"))
		if !strings.HasSuffix(name, ".json") || err != nil || pid <= 0 || !alive(pid) {
			continue
		}
		b, err := os.ReadFile(filepath.Join(dir, name))
		var rec struct {
			Cwd        string `json:"cwd"`
			SessionID  string `json:"sessionId"`
			Status     string `json:"status"`
			WaitingFor string `json:"waitingFor"`
			UpdatedAt  int64  `json:"updatedAt"`
		}
		if err != nil || json.Unmarshal(b, &rec) != nil {
			unknown = true // a half-written file, or one that cannot be read: this may be the session
			continue
		}
		if !sameDir(rec.Cwd, wt) {
			continue
		}
		if best.Pid == 0 || rec.UpdatedAt > bestAt {
			best, bestAt = sessionRec{Pid: pid, SessionID: rec.SessionID, Status: rec.Status, WaitingFor: rec.WaitingFor, UpdatedAt: rec.UpdatedAt}, rec.UpdatedAt
		}
	}
	switch {
	case best.Pid != 0:
		return best, sessionFound
	case unknown:
		return sessionRec{}, sessionUnknown
	}
	return sessionRec{}, sessionNone
}

func sameDir(a, b string) bool {
	if a == "" || b == "" {
		return false
	}
	ra, errA := filepath.EvalSymlinks(a)
	rb, errB := filepath.EvalSymlinks(b)
	if errA != nil || errB != nil {
		return filepath.Clean(a) == filepath.Clean(b)
	}
	return ra == rb
}

// termRun is lifecycle.TermSessions over the session files: what a handoff reads of a terminal agent, and the message it tells one by.
type termRun struct{ p *Parts }

var _ lifecycle.TermSessions = termRun{}

func (t termRun) Session(agent string) (int, string) {
	if t.p.Kind(agent) {
		return 0, ""
	}
	switch rec, _, found := t.p.terminalSession(agent, t.p.LC.term.LiveTerminals()); {
	case found == sessionNone:
		return 0, ""
	case found == sessionUnknown:
		return 0, "unknown"
	case rec.Status == "":
		return rec.Pid, "unknown"
	default:
		return rec.Pid, rec.Status
	}
}

func (termRun) PidGone(pid int) bool { return !alive(pid) }

// PidStart reads the process's start time from ps; 0 when it cannot be read.
func (termRun) PidStart(pid int) int64 {
	out, err := exec.Command("ps", "-o", "lstart=", "-p", strconv.Itoa(pid)).Output()
	if err != nil {
		return 0
	}
	t, err := time.ParseInLocation("Mon Jan _2 15:04:05 2006", strings.Join(strings.Fields(string(out)), " "), time.Local)
	if err != nil {
		return 0
	}
	return t.Unix()
}

// PaneHolds walks the parents of pid up to the pane's process (tmux's pane_pid).
func (termRun) PaneHolds(handle string, pid int) (bool, bool) {
	out, err := exec.Command("tmux", "-L", paneSocket(), "display-message", "-p", "-t", handle, "#{pane_pid}").Output()
	panePid, perr := strconv.Atoi(strings.TrimSpace(string(out)))
	if err != nil || perr != nil || panePid <= 0 {
		return false, false
	}
	table, err := exec.Command("ps", "-axo", "pid=,ppid=").Output()
	if err != nil {
		return false, false
	}
	parent := map[int]int{}
	for _, line := range strings.Split(string(table), "\n") {
		f := strings.Fields(line)
		if len(f) != 2 {
			continue
		}
		a, e1 := strconv.Atoi(f[0])
		b, e2 := strconv.Atoi(f[1])
		if e1 == nil && e2 == nil {
			parent[a] = b
		}
	}
	if _, running := parent[pid]; !running {
		return false, false
	}
	for p, n := pid, 0; p > 1 && n < 64; p, n = parent[p], n+1 {
		if p == panePid {
			return true, true
		}
	}
	return false, true
}

func paneSocket() string {
	if s := os.Getenv("TMUX_SOCKET"); s != "" {
		return s
	}
	return "ocp"
}

func (t termRun) Tell(agent, _, text string) error {
	sess, err := findSession(t.p.home, t.p.db, agent)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute) // the agent's lock is held
	defer cancel()
	if res := peerinbox.New().Deliver(ctx, sess, text, peerFrom(t.p.home, t.p.db)(agent, supervisor.DaemonOp)); res.Kind != peerinbox.Delivered {
		return errors.New(res.Why)
	}
	return nil
}
