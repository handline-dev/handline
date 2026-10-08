package lifecycle

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/textsafe"
)

const (
	// SubmitWaitS is how long to wait for the agent to be ready, and then for its own hook to confirm it got the first task: one number,
	// since they describe the same patience.
	SubmitWaitS = 60
	// StartedTimeoutS is how long to wait for a launched agent to show up in the process table before saying it did not: `hl launch`
	// execs claude, which takes a few seconds to be visible.
	StartedTimeoutS = 20
	// RunKey is the meta name of the orchestration Run this team's work hangs under: one per installation.
	RunKey = "orca:run"
)

// gitTimeout is how long a git command may run; a var so a test can shorten it.
var gitTimeout = 120 * time.Second

// Host is the terminal host's worktree, terminal and Run calls (session/term.py, the part the daemon's terminal channel has not
// brought over yet).
type Host interface {
	Available() bool
	Missing() string
	CreateWorktree(repo, name, baseRef string) (path, failed string)
	// OpenTerminal runs cmd in a new terminal in cwd; handle "" with why "" is a terminal started whose handle could not be read.
	OpenTerminal(cmd, cwd, title string) (handle, why string)
	// Manual is the host's own commands for the steps, for a dry run.
	Manual(wt, repo, name, baseRef, command string) string
	WaitIdle(handle string, timeoutMs int) bool
	Submit(handle, prompt string, timeoutS int)
	HasRuns() bool
	RunCurrent() string
	RunExists(run string) bool
	RunUse(run string)
	RunCreate(title string) string
	TaskCreate(spec, title, displayName, run, parent string) string
	Dispatch(task, handle, run string) string
}

// Spawns is team/spawn.py: giving an agent its own worktree and starting it there. The agent name is the join key across every system:
// the worktree name, the task label and the folder name are all the same string. Choosing the base branch is the other thing that is
// easy to get wrong: an isolated worktree takes origin/<base> by default, so if local <base> is ahead the new worktree silently loses
// those commits. It checks which side leads and branches from that one, and refuses when they have diverged.
type Spawns struct {
	Q        db.Q
	Host     Host
	Launch   Launch
	Delivery Delivery
	Roster   *team.Roster
	Team     func() *team.Team
	Sleep    func(time.Duration)
	Now      func() time.Time
	// Run is the structured sessions; nil keeps every agent a terminal agent.
	Run   Runner
	NewID func() string
	Locks *AgentLocks
}

var _ Spawner = (*Spawns)(nil)

func (s *Spawns) sleep(d time.Duration) {
	if s.Sleep != nil {
		s.Sleep(d)
		return
	}
	time.Sleep(d)
}

func (s *Spawns) now() time.Time {
	if s.Now != nil {
		return s.Now()
	}
	return time.Now()
}

// confirmSession is "" once a session is actually running for agent; otherwise the warning StartIn and Spawn return instead of
// "started": a terminal handle back from the host is not a session - oh-my-zsh's own update prompt once ate a launch command whole and
// left a bare shell, "started" regardless.
func (s *Spawns) confirmSession(agent string) string {
	if s.Delivery.WaitForSession(agent, StartedTimeoutS, "") {
		return ""
	}
	return fmt.Sprintf("no session is running in it after %ds - the terminal is there and claude is not. Go and look (t): whatever it printed is on that shell.", StartedTimeoutS)
}

// git runs git as (exit code, stdout, stderr), each cleaned at the source so a caller that embeds them in a message it returns need not
// sanitise it first. The error is git not running at all (not installed, no such directory, timed out), which Python's _run raises.
func git(cwd string, args ...string) (int, string, string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), gitTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, "git", args...)
	cmd.Dir = cwd
	var out, errb strings.Builder
	cmd.Stdout, cmd.Stderr = &out, &errb
	code := 0
	if err := cmd.Run(); err != nil {
		if ctx.Err() != nil {
			return 0, "", "", fmt.Errorf("git %s: timed out after %s", strings.Join(args, " "), gitTimeout)
		}
		if ee, ok := err.(*exec.ExitError); ok {
			code = ee.ExitCode()
		} else {
			return 0, "", "", fmt.Errorf("git %s: %w", strings.Join(args, " "), err)
		}
	}
	return code, textsafe.Oneline(strings.TrimSpace(out.String())), textsafe.Oneline(strings.TrimSpace(errb.String())), nil
}

func hasCommit(repo, ref string) (bool, error) {
	code, _, _, err := git(repo, "rev-parse", "--verify", "--quiet", ref+"^{commit}")
	return code == 0 && err == nil, err
}

// PickBase is the ref to branch from and why. An empty ref means the two have diverged, or there is nothing to branch from: a human has
// to act.
func PickBase(repo, base string) (ref, why string, err error) {
	if _, _, _, err := git(repo, "fetch", "-q", "origin"); err != nil {
		return "", "", err
	}
	have, err := hasCommit(repo, base)
	if err != nil {
		return "", "", err
	}
	if !have {
		// `git worktree add ... <base>` would die on the name (legacy repos have no main).
		if have, err := hasCommit(repo, "origin/"+base); err != nil || have {
			return "origin/" + base, fmt.Sprintf("no local %s; using origin/%s", base, base), err
		}
		_, head, _, err := git(repo, "symbolic-ref", "--short", "-q", "HEAD")
		if err != nil {
			return "", "", err
		}
		if head != "" {
			if have, err := hasCommit(repo, head); err != nil || have {
				return head, fmt.Sprintf("no %s here; using the checked-out %s", base, head), err
			}
		}
		return "", fmt.Sprintf("%s has no %s branch and no commit to branch from - make a first commit there", repo, base), nil
	}
	code, out, _, err := git(repo, "rev-list", "--left-right", "--count", fmt.Sprintf("origin/%s...%s", base, base))
	if err != nil {
		return "", "", err
	}
	if code != 0 {
		return base, fmt.Sprintf("no origin/%s to compare against; using local %s", base, base), nil
	}
	f := strings.Fields(out)
	var remote, local int
	var err1, err2 error
	if len(f) == 2 {
		remote, err1 = strconv.Atoi(f[0])
		local, err2 = strconv.Atoi(f[1])
	}
	if len(f) != 2 || err1 != nil || err2 != nil {
		return base, fmt.Sprintf("could not read the comparison (%s); using local %s", pyRepr(out), base), nil
	}
	switch {
	case remote > 0 && local > 0:
		return "", fmt.Sprintf("local %s and origin/%s have diverged (%d local, %d remote). Merge origin/%s into %s first, then branch from the result - picking a side would lose work.", base, base, local, remote, base, base), nil
	case remote > 0:
		return "origin/" + base, fmt.Sprintf("origin/%s leads by %d", base, remote), nil
	case local > 0:
		return base, fmt.Sprintf("local %s leads by %d", base, local), nil
	}
	return base, fmt.Sprintf("%s and origin/%s agree", base, base), nil
}

// pyRepr is repr() of a str, as far as the comparison output needs it.
func pyRepr(s string) string {
	if !strings.Contains(s, "'") {
		return "'" + s + "'"
	}
	if !strings.Contains(s, `"`) {
		return `"` + s + `"`
	}
	return "'" + strings.ReplaceAll(s, "'", `\'`) + "'"
}

// PromptLanded waits until the agent's own hook reports a submitted prompt. The host reports that input was accepted, which is not the
// same as submitted - for the Claude TUI it cannot observe the difference at all. UserPromptSubmit is the agent itself saying it received
// a task. It matches any prompt for that agent, so a human typing into the terminal first would also satisfy it: the question being
// asked is whether this agent is taking instructions at all.
func (s *Spawns) PromptLanded(agent string, sinceID int64, timeout time.Duration) (bool, error) {
	deadline := s.now().Add(timeout)
	for {
		yes, err := db.HasEventAfter(s.Q, sinceID, agent, "UserPromptSubmit")
		if err != nil || yes {
			return yes, err
		}
		if !s.now().Before(deadline) {
			return false, nil
		}
		s.sleep(2 * time.Second)
	}
}

// WorktreePath is the directory an agent works in, out of what its row holds, "" when it is gone. `hl launch` records what the host
// handed it, which is a selector - the repo's id, then `::`, then the path - while spawn records a plain path. Testing the selector as a
// path finds nothing, so an agent that still had its worktree was given a second one beside it.
func (s *Spawns) WorktreePath(recorded string) string { return WorktreePath(recorded) }

// WorktreePath is Spawns.WorktreePath without the tool.
func WorktreePath(recorded string) string {
	if recorded == "" {
		return ""
	}
	path := recorded
	if i := strings.Index(recorded, "::"); i >= 0 {
		path = recorded[i+2:]
	}
	if st, err := os.Stat(path); err == nil && st.IsDir() {
		return path
	}
	return ""
}

// ExtraPaths is the agent's further worktrees that are still there, out of what its row holds. A path gone is not listed (and
// EnsureExtras makes it again).
func ExtraPaths(row *db.AgentState) []string {
	if row == nil || row.ExtraWorktrees.String == "" {
		return nil
	}
	var listed []any
	if json.Unmarshal([]byte(row.ExtraWorktrees.String), &listed) != nil {
		return nil
	}
	var out []string
	for _, x := range listed {
		if p, ok := x.(string); ok {
			if wt := WorktreePath(p); wt != "" {
				out = append(out, wt)
			}
		}
	}
	return out
}

// AllPaths is every worktree the agent has: its first, then the others.
func AllPaths(row *db.AgentState) []string {
	var out []string
	if row != nil {
		if first := WorktreePath(row.Worktree.String); first != "" {
			out = append(out, first)
		}
	}
	return append(out, ExtraPaths(row)...)
}

// repoOf is the repository a worktree belongs to (the folder that holds its .git), "" when it cannot be told.
func repoOf(worktree string) (string, error) {
	code, out, _, err := git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")
	if err != nil || code != 0 || out == "" {
		return "", err
	}
	return filepath.Dir(out), nil
}

func (s *Spawns) wantedExtras(agent string) int {
	if s.Roster == nil {
		return 0
	}
	cfg, err := s.Roster.Read(agent)
	if err != nil {
		return 0 // a config that cannot be read asks for nothing here
	}
	return int(team.ExtraWorktrees(cfg))
}

// EnsureExtras makes the worktrees agent.yaml `extra_worktrees` asks for and the agent has not got, beside its first, and records them:
// its further worktrees, and what to tell. Each is `<agent>-2`, `<agent>-3` on a branch of that name from main, which is the host's own
// way of making a worktree; the agent checks out what it wants there. One it has is kept as it is and never made twice, so a restart, a
// wake or the reconciler's recovery cannot make a third or strand the first. Failing to make one is told, not fatal: the agent starts
// with what it has.
func (s *Spawns) EnsureExtras(agent, first, repo string) (have, told []string, err error) {
	want := s.wantedExtras(agent)
	row, err := db.GetAgentState(s.Q, agent)
	if err != nil {
		return nil, nil, err
	}
	have = ExtraPaths(row)
	if len(have) >= want {
		return have[:want], nil, nil
	}
	if repo == "" {
		if repo, err = repoOf(first); err != nil {
			return have, nil, err
		}
	}
	if repo == "" {
		return have, []string{fmt.Sprintf("%s: could not tell which repository %s belongs to, so its extra worktree was not made", agent, first)}, nil
	}
	baseRef, why, err := PickBase(repo, "main")
	if err != nil {
		return have, nil, err
	}
	if baseRef == "" {
		return have, []string{fmt.Sprintf("%s: no extra worktree made: %s", agent, why)}, nil
	}
	for n := len(have) + 2; n < want+2; n++ {
		wt, failed := s.Host.CreateWorktree(repo, fmt.Sprintf("%s-%d", agent, n), baseRef)
		if wt == "" {
			told = append(told, fmt.Sprintf("%s: could not make %s-%d: %s", agent, agent, n, failed))
			break
		}
		have = append(have, wt)
		told = append(told, fmt.Sprintf("%s: made worktree %s (branch %s-%d from %s)", agent, wt, agent, n, baseRef))
	}
	if len(told) > 0 {
		if err := db.SetExtraWorktrees(s.Q, agent, have); err != nil {
			return have, told, err
		}
	}
	return have, told, nil
}

func (s *Spawns) structured(agent string) bool { return s.Run != nil && s.Run.Structured(agent) }

// startStructured opens a structured agent's session in wt. It stands for OpenTerminal, the worker registration and the wait for the
// process table: the session is up when Open returns, and has no terminal to register.
func (s *Spawns) startStructured(agent, wt, prompt string) (bool, error) {
	defer s.Locks.Lock(agent)()
	_, err := (&Sessions{Q: s.Q, Run: s.Run, Launch: s.Launch, NewID: s.NewID}).Start(agent, wt, StartOpts{Cause: "start", Prompt: prompt})
	return err == nil, err
}

// StartIn starts an agent in a worktree it already has. A terminal can be closed by a human, or lost when the host restarts, without
// anything happening to the work: the branch, the checkout and whatever is uncommitted in it are all still there. Making a second
// worktree for that agent would be the wrong repair - it would strand the first. cmd is what the terminal runs; a wake passes its launch
// with the resume id and prompt file, since a closed session's pane is closed with it.
func (s *Spawns) StartIn(agent, worktree, cmd string) (bool, string, error) {
	if s.structured(agent) {
		return s.startInStructured(agent, worktree)
	}
	if !s.Host.Available() {
		return false, s.Host.Missing(), nil
	}
	if _, err := os.Stat(worktree); err != nil {
		return false, fmt.Sprintf("%s: its worktree %s is gone", agent, worktree), nil
	}
	_, extraTold, err := s.EnsureExtras(agent, worktree, "")
	if err != nil {
		return false, "", err
	}
	if cmd == "" {
		if cmd, err = s.Launch.CommandFor(agent, "", "", ""); err != nil {
			return false, "", err
		}
	}
	handle, why := s.Host.OpenTerminal(cmd, worktree, agent)
	if handle == "" && why != "" {
		return false, fmt.Sprintf("%s: could not start in %s: %s", agent, worktree, why), nil
	}
	if handle != "" {
		if err := db.SetTerminal(s.Q, agent, handle, worktree); err != nil {
			return false, "", err
		}
		if _, err := s.RegisterWorker(agent, handle); err != nil {
			return false, "", err
		}
	}
	shown := handle
	if shown == "" {
		shown = "unknown"
	}
	said := strings.Join(append(extraTold, fmt.Sprintf("%s: started in %s (terminal %s)", agent, worktree, shown)), "\n")
	if problem := s.confirmSession(agent); problem != "" {
		return false, said + "\n  but " + problem, nil
	}
	return true, said, nil
}

func (s *Spawns) startInStructured(agent, worktree string) (bool, string, error) {
	if _, err := os.Stat(worktree); err != nil {
		return false, fmt.Sprintf("%s: its worktree %s is gone", agent, worktree), nil
	}
	_, extraTold, err := s.EnsureExtras(agent, worktree, "")
	if err != nil {
		return false, "", err
	}
	if ok, err := s.startStructured(agent, worktree, ""); !ok {
		return false, fmt.Sprintf("%s: could not start in %s: %v", agent, worktree, err), nil
	}
	return true, strings.Join(append(extraTold, fmt.Sprintf("%s: started in %s", agent, worktree)), "\n"), nil
}

// SpawnOpts say how Spawn goes.
type SpawnOpts struct {
	Base, Prompt string
	DryRun       bool
}

// Spawn starts the agent in its worktree: the one it has, else a new one named after it.
func (s *Spawns) Spawn(agent, repo string) (bool, string, error) {
	return s.SpawnWith(agent, repo, SpawnOpts{})
}

// SpawnWith is Spawn with its options.
func (s *Spawns) SpawnWith(agent, repo string, o SpawnOpts) (bool, string, error) {
	if o.Base == "" {
		o.Base = "main"
	}
	structured := s.structured(agent)
	if !structured && !s.Host.Available() {
		return false, s.Host.Missing(), nil
	}
	if s.Roster != nil {
		if _, err := s.Roster.Read(agent); err != nil {
			return false, err.Error(), nil
		}
	}
	// The agent's own worktree if it still has one: `worktree create --name` on a name already in use makes <name>-2 beside it and
	// strands the branch and whatever is uncommitted in the first.
	row, err := db.GetAgentState(s.Q, agent)
	if err != nil {
		return false, "", err
	}
	wt := ""
	if row != nil {
		wt = WorktreePath(row.Worktree.String)
	}
	baseRef, why := "", "its own worktree, kept"
	if wt == "" {
		if baseRef, why, err = PickBase(repo, o.Base); err != nil {
			return false, "", err
		} else if baseRef == "" {
			return false, why, nil
		}
	}
	cmd := ""
	if !structured {
		if cmd, err = s.Launch.CommandFor(agent, "", "", ""); err != nil {
			return false, "", err
		}
	}
	if o.DryRun {
		// A structured session has no terminal steps to list.
		steps := ""
		if !structured {
			steps = "\n  " + s.Host.Manual(wt, repo, agent, baseRef, cmd)
		}
		var text string
		if wt != "" {
			text = fmt.Sprintf("would start in its own worktree %s%s", wt, steps)
		} else {
			text = fmt.Sprintf("would branch from %s (%s)%s", baseRef, why, steps)
		}
		have := len(ExtraPaths(row))
		for n := have + 2; n < s.wantedExtras(agent)+2; n++ {
			text += fmt.Sprintf("\n  would make %s-%d beside it (branch %s-%d from main)", agent, n, agent, n)
		}
		return true, text, nil
	}
	if wt == "" {
		var failed string
		if wt, failed = s.Host.CreateWorktree(repo, agent, baseRef); wt == "" {
			return false, failed, nil
		}
	}
	_, extraTold, err := s.EnsureExtras(agent, wt, repo)
	if err != nil {
		return false, "", err
	}
	if structured {
		return s.spawnStructured(agent, wt, baseRef, why, extraTold, o)
	}
	handle, failed := s.Host.OpenTerminal(cmd, wt, agent)
	if handle == "" && failed != "" {
		return false, fmt.Sprintf("worktree %s is ready, but starting the agent failed: %s", wt, failed), nil
	}
	if handle != "" {
		if err := db.SetTerminal(s.Q, agent, handle, wt); err != nil {
			return false, "", err
		}
		if _, err := s.RegisterWorker(agent, handle); err != nil {
			return false, "", err
		}
	}
	from := "kept"
	if baseRef != "" {
		from = "branched from " + baseRef
	}
	shown := handle
	if shown == "" {
		shown = "unknown"
	}
	lines := []string{fmt.Sprintf("%s: worktree %s", agent, wt), fmt.Sprintf("  %s (%s)", from, why)}
	for _, t := range extraTold {
		lines = append(lines, "  "+t)
	}
	lines = append(lines, "  terminal "+shown)
	if problem := s.confirmSession(agent); problem != "" {
		return false, strings.Join(append(lines, "  WARNING: "+problem), "\n"), nil
	}
	if o.Prompt != "" && handle == "" {
		// Sending to a handle we could not read means typing into nothing and then blaming the agent's input box for a failure that
		// happened here.
		lines = append(lines, "  WARNING: could not read the terminal handle, so the first task was not sent. The agent is up; send it by hand.")
		return false, strings.Join(lines, "\n"), nil
	}
	if o.Prompt != "" {
		// Typing into a TUI that is still booting loses the opening characters and leaves the remainder sitting in the input box, unsent:
		// three agents came up that way and did nothing at all, while the host had reported the send as accepted. So wait for the agent
		// to be ready, then believe its own UserPromptSubmit rather than the send receipt.
		since, err := db.MaxEventID(s.Q)
		if err != nil {
			return false, "", err
		}
		// A host without this wait would no-op silently and we would type into a booting TUI: the exact bug this exists to stop.
		if !s.Host.WaitIdle(handle, SubmitWaitS*1000) {
			lines = append(lines, "  WARNING: could not wait for the agent to be ready; the first task may have been typed into a booting TUI")
		}
		s.Host.Submit(handle, o.Prompt, 30)
		landed, err := s.PromptLanded(agent, since, SubmitWaitS*time.Second)
		if err != nil {
			return false, "", err
		}
		if !landed {
			// The agent exists and the worktree is real, but it was asked to do something and never got it. Anything driving spawns from
			// a script reads the exit code, not this text.
			lines = append(lines, fmt.Sprintf("  WARNING: %s never reported receiving the task. It is probably sitting in the input box unsent - check the terminal, clear it with an interrupt and send again.", agent))
			return false, strings.Join(lines, "\n"), nil
		}
		lines = append(lines, "  first task submitted")
	}
	return true, strings.Join(lines, "\n"), nil
}

// spawnStructured is Spawn's end for a structured agent: the session opened in the worktree with the first task as its first message.
// The receipt of that message is the delivery, so there is no hook to wait for.
func (s *Spawns) spawnStructured(agent, wt, baseRef, why string, extraTold []string, o SpawnOpts) (bool, string, error) {
	from := "kept"
	if baseRef != "" {
		from = "branched from " + baseRef
	}
	lines := []string{fmt.Sprintf("%s: worktree %s", agent, wt), fmt.Sprintf("  %s (%s)", from, why)}
	for _, t := range extraTold {
		lines = append(lines, "  "+t)
	}
	if ok, err := s.startStructured(agent, wt, o.Prompt); !ok {
		return false, strings.Join(append(lines, fmt.Sprintf("  WARNING: worktree %s is ready, but starting the agent failed: %v", wt, err)), "\n"), nil
	}
	lines = append(lines, "  session started")
	if o.Prompt != "" {
		lines = append(lines, "  first task delivered")
	}
	return true, strings.Join(lines, "\n"), nil
}

// EnsureRun is the Run to register workers in, making one the first time. The host draws a Run as a card with its workers under it, so
// this is what turns a pile of terminals into a team anyone can see. It is remembered rather than recreated: a second Run splits the same
// team across two cards, and `--parent` then refuses because the two tasks are in different Runs. A terminal that is already bound to a
// Run wins, because that binding is what a task lands in whatever `--run` says.
func (s *Spawns) EnsureRun() (string, error) {
	run, _, err := db.Meta(s.Q, RunKey)
	if err != nil {
		return "", err
	}
	if bound := s.Host.RunCurrent(); bound != "" {
		if bound != run {
			if err := db.SetMeta(s.Q, RunKey, bound); err != nil {
				return "", err
			}
		}
		return bound, nil
	}
	if run != "" && s.Host.RunExists(run) {
		s.Host.RunUse(run)
		return run, nil
	}
	if run = s.Host.RunCreate("Handline team"); run != "" {
		return run, db.SetMeta(s.Q, RunKey, run)
	}
	return "", nil
}

// RegisterWorker makes this agent's terminal a worker in the Run, under its supervisor. Without it an agent is one more tab: the host
// has no idea it belongs to a team, which is why an agent asked to get work done reached for a subagent - the only child it knew how to
// make. The tree comes from team.yaml, so the card and the routing cannot disagree about who reports to whom. "" when nothing was
// registered.
func (s *Spawns) RegisterWorker(agent, handle string) (string, error) {
	if !s.Host.HasRuns() {
		return "", nil
	}
	run, err := s.EnsureRun()
	if err != nil || run == "" {
		return "", err
	}
	row, err := db.GetAgentState(s.Q, agent)
	if err != nil {
		return "", err
	}
	if row != nil && row.Task.String != "" {
		// Already a worker; the dispatch binds the (possibly new) terminal.
		return s.Host.Dispatch(row.Task.String, handle, run), nil
	}
	parent := ""
	if s.Team != nil {
		if boss := s.Team().SupervisorOf(agent); boss != "" {
			bossRow, err := db.GetAgentState(s.Q, boss)
			if err != nil {
				return "", err
			}
			if bossRow != nil {
				parent = bossRow.Task.String
			}
		}
	}
	spec := fmt.Sprintf("%s: a Handline agent. Work reaches it as a task (`hl task new --owner %s`); this task is the card it appears in.", agent, agent)
	task := s.Host.TaskCreate(spec, agent, agent, run, parent)
	if task == "" && parent != "" {
		// A parent in another Run - an older card, or one made before this remembered which Run was the team's - is a reason to draw the
		// agent flat, not a reason not to draw it.
		task = s.Host.TaskCreate(spec, agent, agent, run, "")
	}
	if task == "" {
		return "", nil
	}
	if err := db.SetTask(s.Q, agent, task); err != nil {
		return "", err
	}
	return s.Host.Dispatch(task, handle, run), nil
}
