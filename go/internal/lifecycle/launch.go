package lifecycle

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/gitrun"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/sessiontoken"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/team"
)

// DisallowedTools is the tools a session may not use. Work for another agent goes through `hl task new`, where the bridge can see it;
// Agent spawns a subagent that dies with the session, unseen, and SendMessage reaches another agent's own session directly, bypassing
// the bridge. ScheduleWakeup and CronCreate: a self-scheduled wakeup survives the park's /clear and fires into a session at the usage
// wall, and ocp types the answer into the session when it comes, so nothing an agent schedules is needed. Build and Fingerprint name
// the same tools, or a change to one reads as a launch that never drifted to the other.
const DisallowedTools = "Agent,SendMessage,ScheduleWakeup,CronCreate"

// AccountStatus is usage/status.py's reading of one account, as launch uses it. A nil trusted figure is one nothing can name.
type AccountStatus struct {
	Exists, Stale             bool
	AgeS                      *float64
	ShortTrusted, LongTrusted *int
	ShortLabel, LongLabel     string
	ShortReset, LongReset     int64 // 0: none
}

// UsagePort is usage/status.py and usage/load.py as launch reads them.
type UsagePort interface {
	Read(acct string) AccountStatus
	ShortBlock() int
	LongBlock() int
	// PickAccount is the best of the candidates for agent (bootstrap: a never-run account is a real place to go; live refresh: this is
	// the one caller about to place an agent, so a carried or idle-floored candidate is asked fresh); "" when none.
	PickAccount(candidates []string, agent string) string
	// NotFor is whether the account is another vendor's than vendor.
	NotFor(acct, vendor string) bool
	// Fit is whether nothing says the account is a bad place to start: a stale or missing reading is the account nobody has run on
	// lately, and the session about to start is the only thing that writes a fresh one.
	Fit(acct, agent string) bool
	// CapOf is how many agents the account may have mid-turn at once: the configured cap shrunk by its weekly pace (none when it is ahead
	// of its week).
	CapOf(acct string) int
	// Headroom is what the account's weekly bucket still holds as a share of the week's quota, ok false when the pace cannot be read. The
	// larger, the slower-paced.
	Headroom(acct string) (float64, bool)
	// ActiveOn is how many agents are mid-turn on the account, agent itself left out (load.active_agents with exclude): one counted
	// from its wake_reserved event until its Stop counts too. PickAccount and Fit leave agent out of the load the same way
	// (load.read_all(exclude=agent)), so a placement is not refused for the slot the agent being placed holds.
	ActiveOn(q db.Q, acct, agent string) int
}

// AccountsPort is team/accounts.py as launch uses it.
type AccountsPort interface {
	MayStart(acct string) bool
	LoginCommand(acct string) (string, error)
	EnvFor(acct, agent string) map[string]string
}

// Driver is one vendor's binary and how its session is launched (vendors/).
type Driver interface {
	Name() string
	IsResume(extra []string) bool
	Binary() string
	// Hooks is the vendor's state hooks, nil when it has none.
	Hooks() Hooks
	AutoModeArgv() []string
	PermissionModeArgv(mode string) []string
	SkipPermissionsArgv() []string
	LaunchArgv(exe string, o LaunchArgs) []string
	LaunchEnv(preamble, disallowed, agentDir string) map[string]string
	PromptArgv(prompt string) []string
	// TranscriptDir is where a session in worktree keeps its transcripts under a config directory.
	TranscriptDir(configDir, worktree string) (string, bool)
}

// LaunchArgs are what a vendor's launch line is built from.
type LaunchArgs struct {
	Model, Effort, Disallowed string
	MCPArgs                   []string
	Preamble                  string
	Hide, NoWrite             []string
	MemoryDir                 string
	Trusted                   []string // nil: not asked for
	Chrome                    bool
	SandboxOff                bool
	MCPApproved               []string
	ExtraDirs                 []string
}

// Hooks is a vendor's state hooks, which launch keeps current.
type Hooks interface {
	EnsureShared() error
	// Missing is whether a wanted event is not installed.
	Missing() bool
	Install(exe string) error
	AllowHomeSocket() error
}

// VendorsPort is vendors/ as launch uses it.
type VendorsPort interface {
	ForAgent(agent string) (Driver, error)
	AgentVendor(agent string) string
	AccountVendor(acct string) string
	ConfigDir(acct string) string
}

// LaunchPorts are the other groups' calls launch makes.
type LaunchPorts struct {
	Usage    UsagePort
	Accounts AccountsPort
	Vendors  VendorsPort
	// AccountReading is agents.account_reading: which account an agent's usage is landing on, and whether that is recorded, reported or assumed.
	AccountReading func(q db.Q, agent string) (account, source string, ok bool)
	// CurrentAccount is the account recorded for the agent.
	CurrentAccount func(q db.Q, agent string) string
	// WaitingFor is lifecycle.waiting_for: whether anything is waiting to be told to the agent.
	WaitingFor func(q db.Q, agent string) bool
	// TeamRules is the shared/team/<team>.md of each team the agent is in, as "# Rules for team X\n<text>".
	TeamRules func(agent string) []string
	// Routing is team.yaml; nil when there is none.
	Routing func() *team.Team
	// McpApproved is trust.mcp_approved for the launch's working directory.
	McpApproved func(cwd string) []string
	// TerminalHandle and WorktreeID are what the host puts in the environment of every terminal it launches.
	TerminalHandle, WorktreeID func() string
	// RegisterWorker joins the agent's terminal to the team's Run.
	RegisterWorker func(agent, handle string) error
	Warn           func(string)
	Exec           func(argv0 string, argv []string, env []string) error
}

// Refusal is a launch that must not go ahead, in words for the person at the terminal.
type Refusal struct{ Msg string }

func (r *Refusal) Error() string { return r.Msg }

func refuse(format string, a ...any) *Refusal { return &Refusal{fmt.Sprintf(format, a...)} }

// Launcher is session/launch.py: hl launch, the execution layer. The launcher wrapper sets the account's config directory in the
// environment before the vendor's binary starts, which the host preserves across hibernation and restore.
type Launcher struct {
	Q      db.Q
	Home   string
	Roster *team.Roster
	Ports  LaunchPorts
	Now    func() time.Time
	// Tokens is the session proof and key store; Build refuses without it.
	Tokens *sessiontoken.Tokens
}

func (l *Launcher) warn(format string, a ...any) {
	if l.Ports.Warn != nil {
		l.Ports.Warn(fmt.Sprintf(format, a...))
	}
}

func (l *Launcher) now() time.Time {
	if l.Now != nil {
		return l.Now()
	}
	return time.Now()
}

// mayStartAccounts is the accounts that are logged in: `assignable` says nothing about credentials.
func (l *Launcher) mayStartAccounts() []string {
	var out []string
	for _, a := range paths.ListAccounts(l.Home) {
		if l.Ports.Accounts.MayStart(a) {
			out = append(out, a)
		}
	}
	return out
}

// FindRoom is the account that can take this agent's next session, "" for none: the best of the logged-in ones, and failing that one
// nothing says is a bad place to start.
func (l *Launcher) FindRoom(agent string) string {
	candidates := l.mayStartAccounts()
	if picked := l.Ports.Usage.PickAccount(candidates, agent); picked != "" {
		return picked
	}
	vendor := l.Ports.Vendors.AgentVendor(agent)
	for _, a := range candidates {
		if !l.Ports.Usage.NotFor(a, vendor) && l.Ports.Usage.Fit(a, agent) {
			return a
		}
	}
	return ""
}

// ChooseAccount is which account this launch runs on, "" when there is none. The moment a session is made is the only moment its
// account can be chosen, since after execve the config directory is fixed. Resuming is not that moment: a session id belongs to one
// account's config directory, so a resumed session goes back to the account it was recorded on, or the run is not a resume at all.
// account is a destination someone already chose (a move): Build still holds it to the vendor, login and wall checks.
func (l *Launcher) ChooseAccount(agent string, resuming bool, account string) string {
	if resuming {
		return l.Ports.CurrentAccount(l.Q, agent)
	}
	if account != "" {
		return account
	}
	return l.FindRoom(agent)
}

// WakeWait is a closed agent's wake walled behind an account's 5h or weekly wall.
type WakeWait struct {
	Account string
	Pct     int
	Reset   int64 // when the walled window rolls over; 0 unknown
	// Assumed is true when Account is a guess (one written before a launch confirmed it) rather than the account this resume is tied to.
	Assumed bool
	Window  string // the walled window's length as a person says it
	IsLong  bool   // the longest window, not the shortest
	// Slot: no wall, no slot - the account is full. Full is its cap now (0 when it is ahead of its week), Busy the others mid-turn on it,
	// Older the closed agents the daemon wakes first.
	Slot              bool
	Full, Busy, Older int
}

// wall is the window this account is walled behind, the shortest first: an account walled on both reports the shortest one's rollover,
// and once that passes it is refused again on the longest still standing.
func (l *Launcher) wall(acct string, assumed bool) *WakeWait {
	st := l.Ports.Usage.Read(acct)
	if st.ShortTrusted != nil && *st.ShortTrusted >= l.Ports.Usage.ShortBlock() {
		return &WakeWait{Account: acct, Pct: *st.ShortTrusted, Reset: st.ShortReset, Assumed: assumed, Window: st.ShortLabel}
	}
	if st.LongTrusted != nil && *st.LongTrusted >= l.Ports.Usage.LongBlock() {
		return &WakeWait{Account: acct, Pct: *st.LongTrusted, Reset: st.LongReset, Assumed: assumed, Window: st.LongLabel, IsLong: true}
	}
	return nil
}

// full is a resume's wait for a slot on its own account, where it is not moved: none is left once the others on it (agent itself left out)
// fill the cap, or older closed agents waiting for the same account would take what is free. Oldest waiter first.
func (l *Launcher) full(q db.Q, agent string, row *db.AgentState, acct string, assumed bool) (*WakeWait, error) {
	cap, others := l.Ports.Usage.CapOf(acct), l.Ports.Usage.ActiveOn(q, acct, agent)
	rows, err := db.AllAgentStates(q)
	if err != nil {
		return nil, err
	}
	older := 0
	for _, r := range rows {
		if r.Agent == agent || r.ClosedAt.Int64 == 0 || r.ResumeSession.String == "" || r.State == states.Stalled || r.WakeFailures >= states.MaxFailedAttempts {
			continue
		}
		if r.ClosedAt.Int64 > row.ClosedAt.Int64 || (r.ClosedAt.Int64 == row.ClosedAt.Int64 && r.Agent >= agent) {
			continue
		}
		if l.Ports.CurrentAccount(q, r.Agent) == acct && l.Ports.WaitingFor(q, r.Agent) {
			older++
		}
	}
	if others+older < cap {
		return nil, nil
	}
	return &WakeWait{Account: acct, Assumed: assumed, Window: "slot", Slot: true, Full: cap, Busy: others, Older: older}, nil
}

// SlowestPaced is the account a closed agent moves to when its own (skip) is full and its cache is cold: of the other accounts that may
// start, take this vendor, fit, and have a free slot the way a resume's wait judges it (the others mid-turn plus the older closed agents
// waiting there, so a move does not jump their queue), the one furthest behind its week. "" when none has room.
func (l *Launcher) SlowestPaced(q db.Q, agent string, row *db.AgentState, skip string) string {
	vendor := l.Ports.Vendors.AgentVendor(agent)
	best, most := "", math.Inf(-1)
	for _, a := range l.mayStartAccounts() {
		if a == skip || l.Ports.Usage.NotFor(a, vendor) || !l.Ports.Usage.Fit(a, agent) {
			continue
		}
		if w, err := l.full(q, agent, row, a, false); err != nil || w != nil {
			continue
		}
		h, ok := l.Ports.Usage.Headroom(a)
		if !ok {
			h = 0 // a pace nobody can read is not evidence of room beyond the burst
		}
		if h > most {
			best, most = a, h
		}
	}
	return best
}

// FreesFirst is, for an agent no account has room for, the walled account whose wall lifts first; nil when none is walled.
func (l *Launcher) FreesFirst(agent string) *WakeWait {
	vendor := l.Ports.Vendors.AgentVendor(agent)
	var best *WakeWait
	rank := func(w *WakeWait) float64 {
		if w.Reset == 0 {
			return math.Inf(1)
		}
		return float64(w.Reset)
	}
	for _, a := range l.mayStartAccounts() {
		if l.Ports.Usage.NotFor(a, vendor) {
			continue
		}
		if w := l.wall(a, false); w != nil && (best == nil || rank(w) < rank(best)) {
			best = w
		}
	}
	return best
}

// NoRoom is why no account can take this agent, naming the one that frees up first.
func (l *Launcher) NoRoom(agent string) string {
	named := paths.ListAccounts(l.Home)
	if len(l.mayStartAccounts()) == 0 {
		if len(named) == 0 {
			return "no account is registered - `hl account add <name>` first"
		}
		cmd, _ := l.Ports.Accounts.LoginCommand(named[0])
		return fmt.Sprintf("no account is logged in. A human has to do this once (ocp never touches credentials):\n\n  %s\n", cmd)
	}
	if w := l.FreesFirst(agent); w != nil {
		return "no account can take one more agent - " + DescribeWakeWait(w)
	}
	return "no account can take one more agent"
}

// WakeWait is whether a closed agent's wake is walled behind its own account's 5h or weekly wall: the check made before anything is
// typed into a closed agent's terminal. A resume cannot follow an account change, but the pinning rescue can start fresh elsewhere, so
// a wait is not final. It answers only "is the account full", not "is anyone trying to wake this agent". A start with no session to
// resume goes wherever ChooseAccount finds room; with none it waits on the account that frees up first. The resume branch is pinned to
// the account its session is tied to. A computed condition, not a stored one: called again a moment later, on an account whose window
// has since rolled over, it returns nil.
func (l *Launcher) WakeWait(q db.Q, agent string, row *db.AgentState) *WakeWait {
	if !isClosed(row) {
		return nil
	}
	if row.ResumeSession.String != "" {
		acct, source, ok := l.Ports.AccountReading(q, agent)
		if !ok {
			return nil
		}
		if w := l.wall(acct, source == "assumed"); w != nil {
			return w
		}
		return must(l.full(q, agent, row, acct, source == "assumed"))
	}
	if l.ChooseAccount(agent, false, "") != "" {
		return nil
	}
	return l.FreesFirst(agent)
}

// WakeWaitReason is the sentence ps, triggers and the daemon's own log line print for a walled wake: one wording, so a screen can
// never say something the daemon does not act on. "" when it is not walled.
func (l *Launcher) WakeWaitReason(q db.Q, agent string, row *db.AgentState) string {
	if w := l.WakeWait(q, agent, row); w != nil {
		return DescribeWakeWait(w)
	}
	return ""
}

// WaitClock is when the walled window rolls over: a clock for the shortest one, a date and clock for the longest, which is days away.
func WaitClock(w *WakeWait, unknown string) string {
	if w.Reset == 0 {
		return unknown
	}
	layout := "15:04"
	if w.IsLong {
		layout = "01-02 15:04"
	}
	return time.Unix(w.Reset, 0).Local().Format(layout)
}

// DescribeWakeWait is the wording for a wait already computed, so a caller that holds one does not recompute it.
func DescribeWakeWait(w *WakeWait) string {
	if w.Slot && w.Full == 0 {
		return fmt.Sprintf("waiting for %s: it is ahead of its week and takes no new agent", w.Account)
	}
	if w.Slot {
		first := ""
		if w.Older > 0 {
			first = fmt.Sprintf(", %d older waiting go first", w.Older)
		}
		return fmt.Sprintf("waiting for a slot on %s (%d of %d mid-turn%s)", w.Account, w.Busy, w.Full, first)
	}
	who := w.Account
	if w.Assumed {
		who = "assumed account " + w.Account
	}
	return fmt.Sprintf("waiting for %s's %s window (%d%%, rolls over %s)", who, w.Window, w.Pct, WaitClock(w, "unknown"))
}

// mcpConfigArg is `--mcp-config <file>` for the shared mcp.json, if it exists and is a valid {"mcpServers": {...}} object. Not strict:
// passing this leaves an account's own servers loading too. A missing file changes nothing. An invalid one must not stop the launch (a
// typo in a shared file would otherwise wall off every agent), so the caller only warns.
func (l *Launcher) mcpConfigArg() (argv []string, problem string) {
	p := paths.MCPConfig(l.Home)
	b, err := os.ReadFile(p)
	if os.IsNotExist(err) {
		return nil, ""
	}
	var data map[string]any
	if err == nil {
		err = json.Unmarshal(b, &data)
	}
	if err != nil {
		return nil, fmt.Sprintf("%s is invalid (%v)", p, err)
	}
	// Well-formed JSON that is not claude's shape still makes `claude` exit before the session starts: the exact wall-off this skip
	// exists to avoid. Server entries themselves need no checking.
	if _, ok := data["mcpServers"].(map[string]any); !ok {
		return nil, fmt.Sprintf("%s is invalid (not a {\"mcpServers\": {...}} object)", p)
	}
	return []string{"--mcp-config", p}, ""
}

// mcpConfigMaterial is what Fingerprint hashes for the shared mcp.json: whether a launch would pass --mcp-config at all, and if so the
// file's content, so editing it in place reads as drift even though the path never changes. Missing and invalid hash the same.
func (l *Launcher) mcpConfigMaterial() string {
	if argv, _ := l.mcpConfigArg(); len(argv) == 0 {
		return "absent"
	}
	b, err := os.ReadFile(paths.MCPConfig(l.Home))
	if err != nil {
		return "absent"
	}
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

func (l *Launcher) git(worktree string, args ...string) string {
	r := gitrun.Run(worktree, args...)
	if r.RC != 0 {
		return ""
	}
	return strings.TrimSpace(r.Stdout)
}

// MainRefFiles are the files git rewrites to move the repository's main branch, which a session may not write: its ref, the packed
// refs, and the lock files beside them. The sandbox lets a session write the repository's .git for its own commits; this keeps the
// daemon the only mover of main.
func (l *Launcher) MainRefFiles(worktree string) []string {
	common := l.git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")
	if common == "" {
		return nil
	}
	ref := filepath.Join(common, "refs", "heads", gitrun.IntegrationBranch)
	packed := filepath.Join(common, "packed-refs")
	return []string{ref, ref + ".lock", packed, packed + ".lock"}
}

// RosterFiles are the files that say who the agents are, who may ask whom and what each is told, which only the founder's path writes:
// team.yaml and its backup, and every agent's agent.yaml, ROLE.md and base.md. Home is writable to a session, so these are denied by
// name, plus one glob each so an agent made after this launch is covered too.
func (l *Launcher) RosterFiles() []string {
	dir := paths.AgentsDir(l.Home)
	per := []string{"agent.yaml", "ROLE.md", "base.md"}
	tf := paths.TeamFile(l.Home)
	out := []string{tf, tf + ".bak"}
	for _, n := range paths.ListAgents(l.Home) {
		for _, f := range per {
			out = append(out, filepath.Join(dir, n, f))
		}
	}
	for _, f := range per {
		out = append(out, filepath.Join(dir, "*", f))
	}
	return out
}

// HiddenMemories are the memory folders agent may not read: every other agent's, with no exception for its reports.
func (l *Launcher) HiddenMemories(agent string) []string {
	var out []string
	for _, a := range paths.ListAgents(l.Home) {
		if a != agent {
			out = append(out, filepath.Join(paths.AgentDir(l.Home, a), "memory"))
		}
	}
	return out
}

var githubRemote = regexp.MustCompile(`github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?\s`)

// TrustedEnvironment is what the permission classifier should count as the team's own, as the prose it reads: the state folder, this
// worktree and its repository, and the GitHub repos its remotes name. Anything else stays outside the boundary.
func (l *Launcher) TrustedEnvironment(worktree string) []string {
	lines := []string{fmt.Sprintf("The team's state folder %s and everything under it is the team's own: reading and writing it, and running `hl`, is routine.", l.Home)}
	if worktree == "" {
		return lines
	}
	lines = append(lines, fmt.Sprintf("This agent's own git worktree is %s: `cd`, editing files, `sed -i`, running tests and committing inside it are routine.", worktree))
	if common := l.git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir"); common != "" {
		lines = append(lines, fmt.Sprintf("Its repository is %s, and the sibling worktrees under %s are other agents of the same team.", filepath.Dir(common), filepath.Dir(worktree)))
	}
	seen := map[string]bool{}
	var repos []string
	for _, line := range strings.Split(l.git(worktree, "remote", "-v"), "\n") {
		if m := githubRemote.FindStringSubmatch(line + " "); m != nil && !seen[m[1]] {
			seen[m[1]] = true
			repos = append(repos, m[1])
		}
	}
	sort.Strings(repos)
	if len(repos) > 0 {
		var sites []string
		for _, r := range repos {
			sites = append(sites, "github.com/"+r)
		}
		lines = append(lines, "Source control: the GitHub repos "+strings.Join(sites, ", ")+" and their branches.")
	}
	return lines
}

// HiddenTranscripts are the transcript folders agent may not read: every other agent's except its own and its reports', in every
// account of this vendor, since an agent moves between accounts. Only agents with a worktree on record can be named.
func (l *Launcher) HiddenTranscripts(agent string, drv Driver) ([]string, error) {
	var tm *team.Team
	if l.Ports.Routing != nil {
		tm = l.Ports.Routing()
	}
	rows, err := db.AllAgentStates(l.Q)
	if err != nil {
		return nil, err
	}
	var configs []string
	for _, a := range paths.ListAccounts(l.Home) {
		if l.Ports.Vendors.AccountVendor(a) == drv.Name() {
			configs = append(configs, l.Ports.Vendors.ConfigDir(a))
		}
	}
	var out []string
	for _, r := range rows {
		if r.Agent == agent || tm.SupervisorOf(r.Agent) == agent || r.Worktree.String == "" {
			continue
		}
		first := r.Worktree.String
		if i := strings.Index(first, "::"); i >= 0 {
			first = first[i+2:]
		}
		for _, cfg := range configs {
			for _, wt := range append([]string{first}, ExtraPaths(r)...) {
				if d, ok := drv.TranscriptDir(cfg, wt); ok {
					out = append(out, d)
				}
			}
		}
	}
	return out, nil
}

// ExtraWorktreesOf are the agent's further worktrees a session may work in: as many as agent.yaml asks for, and only ones that are there.
func (l *Launcher) ExtraWorktreesOf(agent string, cfg team.Config) ([]string, error) {
	n := int(team.ExtraWorktrees(cfg))
	if n == 0 {
		return nil, nil
	}
	row, err := db.GetAgentState(l.Q, agent)
	if err != nil {
		return nil, err
	}
	have := ExtraPaths(row)
	if len(have) > n {
		have = have[:n]
	}
	return have, nil
}

// worktreesLine says which of the agent's worktrees is which, when it has more than one.
func (l *Launcher) worktreesLine(agent string) (string, error) {
	cfg, err := l.Roster.Read(agent)
	if err != nil {
		return "", nil // a config that cannot be read says nothing here
	}
	extra, err := l.ExtraWorktreesOf(agent, cfg)
	if err != nil || len(extra) == 0 {
		return "", nil
	}
	row, err := db.GetAgentState(l.Q, agent)
	if err != nil {
		return "", err
	}
	first := ""
	if row != nil {
		first = row.Worktree.String
	}
	if i := strings.Index(first, "::"); i >= 0 {
		first = first[i+2:]
	}
	verb := "are"
	if len(extra) == 1 {
		verb = "is"
	}
	return fmt.Sprintf("You have %d worktrees of this repo, all yours: %s (where you were started) is for probes and checking out other shas; %s %s for suite runs, so a long run there is not disturbed when you check out something else in the first. Stay inside these.",
		len(extra)+1, first, strings.Join(extra, ", "), verb), nil
}

// staticPreamble is the part of ContextFor that a rebuild reproduces byte for byte: everything except the handoff, which is supposed to
// differ every time this agent is parked; Fingerprint hashes only this part.
func (l *Launcher) staticPreamble(agent string) (string, error) {
	d := paths.AgentDir(l.Home, agent)
	parts := []string{
		fmt.Sprintf("You are `%s`, one agent in a Handline team. Your name is also in $OCP_AGENT.", agent),
		"Stay inside your own worktree. To read another branch's version of a file, use `git show <ref>:<path>` - it needs nothing outside the worktree. Copying a file out to /tmp to read it stops you on a permission prompt only a human can answer, and you will sit there until one does.",
		"On a long request, keep `hl progress \"done: ... left: ...\"` current: your handoff carries it to the session that takes over.",
	}
	where, err := l.worktreesLine(agent)
	if err != nil {
		return "", err
	}
	if where != "" {
		parts = append(parts[:2], append([]string{where}, parts[2:]...)...)
	}
	if line := l.yourTeam(agent); line != "" {
		parts = append(parts, line)
	}
	for _, f := range [][2]string{{"ROLE.md", "role"}, {"base.md", "standing context"}} {
		if b, err := os.ReadFile(filepath.Join(d, f[0])); err == nil {
			if text := strings.TrimSpace(string(b)); text != "" {
				parts = append(parts, fmt.Sprintf("# Your %s (%s)\n%s", f[1], f[0], text))
			}
		}
	}
	parts = append(parts, l.Ports.TeamRules(agent)...)
	return strings.Join(parts, "\n\n"), nil
}

// ContextFor is ROLE.md + base.md + the handoff waiting for it, as one system-prompt appendix.
func (l *Launcher) ContextFor(agent string) (string, error) {
	static, err := l.staticPreamble(agent)
	if err != nil {
		return "", err
	}
	parts := []string{static}
	text, err := db.GetHandoff(l.Q, agent)
	if err != nil {
		return "", err
	}
	if text != "" {
		parts = append(parts, "# Where you left off (your handoff)\nYou were interrupted and resumed. This is your own record.\n"+strings.TrimSpace(text))
	}
	var out []string
	for _, p := range parts {
		if p != "" {
			out = append(out, p)
		}
	}
	return strings.Join(out, "\n\n"), nil
}

// yourTeam is who this agent answers to and who answers to it, read from team.yaml. Generated rather than written into each ROLE.md: a
// role that named its colleagues by hand kept naming them after they were retired and renamed.
func (l *Launcher) yourTeam(agent string) string {
	var t *team.Team
	if l.Ports.Routing != nil {
		t = l.Ports.Routing()
	}
	if t == nil {
		return ""
	}
	boss := t.SupervisorOf(agent)
	if boss == "" {
		boss = db.Human
	}
	var reports []string
	for _, n := range paths.ListAgents(l.Home) {
		if n != agent && t.SupervisorOf(n) == agent {
			reports = append(reports, n)
		}
	}
	report := "You report to `" + boss + "`. A task you were handed ends with `hl task done T<n> \"<evidence>\"` or `hl task handoff T<n> <agent> \"<note>\"` - that is your report, even a deferral: the next task arrives only then, and an answer typed in chat reaches no one."
	if boss != db.Human {
		// A decision for the human is a task of theirs, so it is named once, below, for the decisions that are theirs.
		report += " `hl say " + boss + " \"...\"` is a message that needs no answer. Being blocked, a decision only they can make, or work nobody handed you is a task for them: `hl task new --owner " + boss + " -t <time> \"<title>\" \"<body>\"`."
	}
	lines := []string{"# Your team", report,
		"Talk to people only through ocp; what you write in your terminal reaches no one. Keep it short. Reply when a message asks you something or you owe a result; a message that only informs needs no reply, except the founder's."}
	if len(reports) > 0 {
		var quoted []string
		for _, n := range reports {
			quoted = append(quoted, "`"+n+"`")
		}
		lines = append(lines, "Your reports: "+strings.Join(quoted, ", ")+". You do not implement - give one work with `hl task new --owner <name> -t <time> \"<title>\" \"<what to do, and what done looks like>\"`, and read what it sends back. A message to a report is `hl say <name> ... --task T<n>` (a memo on that task). One task at a time; correct one with `hl task memo T<n> add \"...\"`, not a message behind it, and throw a stale one away with `hl task discard T<n> \"<why>\"`. Record a wait as `--blocked-by T<n>` on the task, not in prose. Check main before you assign, so nobody rebuilds what landed. Quote the founder's own words in the task you open for their request, and answer the founder through `hl`, never in chat.")
		lines = append(lines, "Finding the bottleneck is your job. At each periodic check, look at each report's queued count, its oldest wait and the work blocked behind it, and compare with the last check. When a wait passes 1h or a queue grows two checks in a row, ask that agent what slows it and open the work that fixes the cause. No dead or stalled agent does not mean healthy.")
	}
	lines = append(lines, "`hl team` shows who you may reach and what each is doing now. A decision or a block only the founder can clear is a task of theirs: `hl task new --owner human -t <time> \"<title>\" \"<body>\"`.")
	// A self-scheduled wakeup survives the park's /clear and fires into a session that is at the usage wall.
	lines = append(lines, "Never schedule your own wakeup, /loop or poll to wait for a reply or a review: ocp types the answer into your session when it comes.")
	return strings.Join(lines, "\n")
}

func sha256Hex(s string) string {
	sum := sha256.Sum256([]byte(s))
	return hex.EncodeToString(sum[:])
}

func (l *Launcher) effortOf(cfg team.Config) string {
	if e, ok := cfg["effort"].(string); ok && e != "" {
		return e
	}
	return team.DefaultEffort
}

func modelOf(cfg team.Config) string {
	if m, ok := cfg["model"].(string); ok {
		return m
	}
	return ""
}

func (l *Launcher) plainMode(agent string, cfg team.Config) bool {
	m := l.Roster.ModeOf(agent, cfg)
	return m == "bypass" || m == team.DefaultMode
}

// Fingerprint is what Build would launch this agent with right now, boiled down to the pieces a running session cannot pick up on its
// own: the model, the effort level, the disallowed-tools list, the shared mcp.json, whether skip-permissions is in effect, and the
// static preamble. Never the account or the handoff - the account is chosen fresh on every real launch, and the handoff is supposed to
// be different every time this agent is parked. Recorded at launch time and compared by Drifted before typing a handoff into a session
// that is still open: a row with nothing recorded disagrees too.
func (l *Launcher) Fingerprint(agent string) (string, error) {
	cfg, err := l.Roster.Read(agent)
	if err != nil {
		return "", err
	}
	static, err := l.staticPreamble(agent)
	if err != nil {
		return "", err
	}
	material := strings.Join([]string{modelOf(cfg), l.effortOf(cfg), DisallowedTools, l.mcpConfigMaterial(), static}, "\x1e")
	if l.Roster.SkipPermissionsInEffect(agent, cfg) {
		material += "\x1eskip-permissions" // only when on: turning it off returns to the old hash
	}
	if !l.plainMode(agent, cfg) {
		material += "\x1emode:" + l.Roster.ModeOf(agent, cfg) // only when not the default, like chrome
	}
	if !l.Roster.SandboxOn(agent, cfg) {
		material += "\x1esandbox-off"
	}
	if mcp := team.MCPConfigArgs(cfg); len(mcp) > 0 {
		material += "\x1emcp:" + mcp[1] // only when set, like chrome
	}
	if team.Chrome(cfg) {
		material += "\x1echrome" // only when on, like skip-permissions
	}
	return sha256Hex(material)[:16], nil
}

// SandboxOffPart is what FingerprintParts records under "sandbox" for a session launched with the sandbox off.
var SandboxOffPart = sha256Hex("off")[:8]

// LaunchedAt is beside the setting hashes in a session's recorded parts: when it was launched, which tells one session from the next
// even when both resume the same transcript.
const LaunchedAt = "launched_at"

// partOrder is the order the parts are written in, as the Python dict keeps it.
var partOrder = []string{"model", "effort", "mcp config", "browser", "permissions", "sandbox", "ROLE.md", "base.md", "team"}

// FingerprintParts is what a launch reads from the settings a person edits, one short hash each, named for the person. Recorded at
// launch beside Fingerprint, and compared by the daemon to name what a running session has not picked up. Not here: what Build reads
// from the machine (the account, the handoff, hidden transcripts, the main-ref files) and the fixed text of the preamble.
func (l *Launcher) FingerprintParts(agent string) (map[string]string, error) {
	cfg, err := l.Roster.Read(agent)
	if err != nil {
		return nil, err
	}
	d := paths.AgentDir(l.Home, agent)
	h := func(s string) string { return sha256Hex(s)[:8] }
	file := func(name string) string {
		b, err := os.ReadFile(filepath.Join(d, name))
		if err != nil {
			return ""
		}
		return strings.TrimSpace(string(b))
	}
	mcp := team.MCPConfigArgs(cfg)
	mcpTail := ""
	if len(mcp) > 0 {
		mcpTail = mcp[1]
	}
	browser := ""
	if team.Chrome(cfg) {
		browser = "on"
	}
	perm := DisallowedTools
	if l.Roster.SkipPermissionsInEffect(agent, cfg) {
		perm += "\x1eskip-permissions"
	}
	if !l.plainMode(agent, cfg) {
		perm += "\x1emode:" + l.Roster.ModeOf(agent, cfg)
	}
	sandbox := h("")
	if !l.Roster.SandboxOn(agent, cfg) {
		sandbox = SandboxOffPart
	}
	return map[string]string{
		"model":       h(modelOf(cfg)),
		"effort":      h(l.effortOf(cfg)),
		"mcp config":  h(l.mcpConfigMaterial() + "\x1e" + mcpTail),
		"browser":     h(browser),
		"permissions": h(perm),
		"sandbox":     sandbox,
		"ROLE.md":     h(file("ROLE.md")),
		"base.md":     h(file("base.md")),
		"team":        h(l.yourTeam(agent) + "\x1e" + strings.Join(l.Ports.TeamRules(agent), "\x1e")),
	}, nil
}

// LaunchRecord is the parts as JSON, in order, with the launch's own stamp, as `launch_parts` keeps them.
func (l *Launcher) LaunchRecord(agent string) (string, error) {
	parts, err := l.FingerprintParts(agent)
	if err != nil {
		return "", err
	}
	var items []string
	for _, k := range partOrder {
		v, _ := json.Marshal(parts[k])
		kk, _ := json.Marshal(k)
		items = append(items, string(kk)+": "+string(v))
	}
	stamp := float64(l.now().UnixNano()) / 1e9
	items = append(items, `"`+LaunchedAt+`": `+strconv.FormatFloat(stamp, 'f', -1, 64))
	return "{" + strings.Join(items, ", ") + "}", nil
}

// LaunchedAtOf is the stamp of the launch this agent_state row records, as written; "" when none.
func LaunchedAtOf(row *db.AgentState) string {
	var parts map[string]json.RawMessage
	if json.Unmarshal([]byte(row.LaunchParts.String), &parts) != nil {
		return ""
	}
	v, ok := parts[LaunchedAt]
	if !ok || string(v) == "null" {
		return ""
	}
	var s string
	if json.Unmarshal(v, &s) == nil {
		return s
	}
	return string(v)
}

// Drifted is whether this agent's running session was launched with a different recipe than Build produces now. A recipe that cannot
// be read is not drift.
func (l *Launcher) Drifted(agent string, row *db.AgentState) bool {
	fp, err := l.Fingerprint(agent)
	if err != nil {
		return false
	}
	return fp != row.LaunchFingerprint.String
}

// TakePrompt reads and removes a prompt file the daemon left for this launch. Taken only after Build has let the launch go ahead: a
// launch that refuses leaves the file, which is how the daemon tells its own session from one somebody else started. Removed as it is
// read, so no later launch picks it up: a prompt belongs to one launch.
func TakePrompt(path string) string {
	if path == "" {
		return ""
	}
	b, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	if os.Remove(path) != nil {
		return ""
	}
	return string(b)
}

// CommandFor is the --command string to hand to the host; account fixes where a move lands, why is what the session's start is
// recorded as, by who asked for it.
func CommandFor(account, why, by, agent string) string {
	exe := paths.OcpExe()
	if v, ok := paths.Getenv("BIN"); ok && v != "" {
		exe = v
	}
	if exe == "" {
		exe = "hl"
	}
	cmd := exe + " launch " + agent
	if account != "" {
		cmd += " --account " + shlexQuote(account)
	}
	if why != "" {
		cmd += " --why " + shlexQuote(why)
	}
	if by != "" {
		cmd += " --by " + shlexQuote(by)
	}
	return cmd
}

// LaunchPort is a Launcher as the Launch the manager and spawn read it through.
type LaunchPort struct{ L *Launcher }

func (p LaunchPort) Drifted(agent string, row *db.AgentState) bool { return p.L.Drifted(agent, row) }

func (p LaunchPort) WakeWait(q db.Q, agent string, row *db.AgentState) *WakeWait {
	return p.L.WakeWait(q, agent, row)
}

func (p LaunchPort) AccountFor(q db.Q, agent string, resuming bool) string {
	return p.L.ChooseAccount(agent, resuming, "")
}

func (LaunchPort) DescribeWakeWait(w *WakeWait) string { return DescribeWakeWait(w) }

func (LaunchPort) CommandFor(agent, account, why, by string) (string, error) {
	return CommandFor(account, why, by, agent), nil
}

// ChooseAccount is whether this agent's next fresh launch has an account to run on.
func (p LaunchPort) ChooseAccount(_ db.Q, agent string) bool {
	return p.L.ChooseAccount(agent, false, "") != ""
}

func (p LaunchPort) NoRoom(agent string) string { return p.L.NoRoom(agent) }
