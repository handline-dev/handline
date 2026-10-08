package lifecycle

import (
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/gitrun"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/textsafe"
)

func (l *Launcher) cwd() string {
	d, _ := os.Getwd()
	return d
}

// Build is the argv and environment ready to hand to execve for agent. extra are the caller's own arguments, force overrides the usage
// wall, account is a destination someone already chose. A launch that must not go ahead is a *Refusal.
func (l *Launcher) Build(agent string, extra []string, force bool, account string) ([]string, map[string]string, error) {
	p := l.Ports
	if l.Tokens != nil {
		l.Tokens.EnsureKeyFolders()
	}
	drv, err := p.Vendors.ForAgent(agent)
	if err != nil {
		return nil, nil, refuse("agent '%s': %v", agent, err)
	}
	resuming := drv.IsResume(extra)
	acct := l.ChooseAccount(agent, resuming, account)
	if acct == "" {
		if resuming {
			return nil, nil, refuse("agent '%s' has no recorded session to resume", agent)
		}
		return nil, nil, refuse("agent '%s': %s", agent, l.NoRoom(agent))
	}
	// The last line before an exec: whatever chose this account, a session is this vendor's binary pointed at that account's credentials.
	if theirs := p.Vendors.AccountVendor(acct); theirs != drv.Name() {
		return nil, nil, refuse("agent '%s' is a %s agent and account '%s' is a %s account - refusing to start it there.", agent, drv.Name(), acct, theirs)
	}

	if hooks := drv.Hooks(); hooks != nil {
		// An unreadable settings file is doctor's to report, not a reason not to start.
		if hooks.EnsureShared() == nil {
			// A wanted event added after the shared settings were last installed would otherwise sit uninstalled for good: every launch is
			// where install gets the chance to catch up.
			if hooks.Missing() {
				if err := hooks.Install(paths.OcpExe()); err != nil {
					l.warn("warning: ocp's own hooks are behind and could not be installed (%v) - run `hl install-hooks` from outside an agent's sandbox", err)
				}
			}
			if err := hooks.AllowHomeSocket(); err != nil {
				l.warn("warning: agents' sandbox cannot reach ocp's socket and it could not be allowed (%v) - run `hl init` from outside an agent's sandbox", err)
			}
		}
	}
	if !p.Accounts.MayStart(acct) {
		login, _ := p.Accounts.LoginCommand(acct)
		return nil, nil, refuse("account '%s' is not logged in.\nA human has to do this once (ocp never touches credentials):\n\n  %s\n", acct, login)
	}

	// Only a current reading may block a launch. Neither a missing nor a stale snapshot can: the session about to start is the only thing
	// that writes one. Staleness still gates assignment, in the usage port.
	st := p.Usage.Read(acct)
	if st.Exists && st.Stale {
		age := "unknown age"
		if st.AgeS != nil {
			age = fmt.Sprintf("%ds", int(*st.AgeS))
		}
		l.warn("warning: account '%s' usage is %s old and may be out of date; the daemon will ask the endpoint about it on its next tick.", acct, age)
	} else if !force && st.Exists && st.ShortTrusted != nil && *st.ShortTrusted >= p.Usage.ShortBlock() {
		reset := "None"
		if st.ShortReset != 0 {
			reset = fmt.Sprint(st.ShortReset)
		}
		return nil, nil, refuse("account '%s' is at %d%% of the %s limit (>= %d%%) - refusing to start.\nresets at %s; use --force to override",
			acct, *st.ShortTrusted, st.ShortLabel, p.Usage.ShortBlock(), reset)
	}

	exe := drv.Binary()
	if exe == "" {
		return nil, nil, refuse("cannot find %s on PATH", drv.Name())
	}

	cfg, err := l.Roster.Read(agent)
	if err != nil {
		return nil, nil, err
	}
	// Always pass effort explicitly: left unset it comes from whatever the environment carries.
	effort := l.effortOf(cfg)
	if !slices.Contains(team.ValidEffort, effort) {
		return nil, nil, refuse("agent '%s' has effort=%q; must be one of %s", agent, effort, strings.Join(team.ValidEffort, ", "))
	}

	mcpArg, mcpErr := l.mcpConfigArg()
	if mcpErr != "" {
		l.warn("warning: shared mcp config %s, skipping --mcp-config", mcpErr)
	}

	preamble, err := l.ContextFor(agent)
	if err != nil {
		return nil, nil, err
	}
	memoryDir := filepath.Join(paths.AgentDir(l.Home, agent), "memory")
	if err := os.MkdirAll(memoryDir, 0o755); err != nil {
		return nil, nil, err
	}
	// A vendor with no auto mode, or an agent whose skip-permissions is in effect, never reads `trusted`.
	chosen := l.Roster.ModeOf(agent, cfg)
	var mode []string
	switch chosen {
	case "bypass":
	case team.DefaultMode:
		mode = drv.AutoModeArgv()
	default:
		mode = drv.PermissionModeArgv(chosen)
	}
	if chosen != "bypass" && chosen != team.DefaultMode && len(mode) == 0 {
		l.warn("warning: agent '%s' has mode %s, but %s has no such mode - starting it as usual", agent, chosen, drv.Name())
	}
	cwd := l.cwd()
	var approved []string
	if p.McpApproved != nil {
		approved = p.McpApproved(cwd)
	}
	extraDirs, err := l.ExtraWorktreesOf(agent, cfg)
	if err != nil {
		return nil, nil, err
	}
	hiddenT, err := l.HiddenTranscripts(agent, drv)
	if err != nil {
		return nil, nil, err
	}
	hide := append(append(hiddenT, l.HiddenMemories(agent)...), l.hiddenFolders()...)
	var trusted []string
	if len(mode) > 0 && chosen == team.DefaultMode {
		trusted = l.TrustedEnvironment(cwd)
	}
	argv := drv.LaunchArgv(exe, LaunchArgs{
		Model: modelOf(cfg), Effort: effort, Disallowed: DisallowedTools,
		MCPArgs:  append(append([]string{}, mcpArg...), team.MCPConfigArgs(cfg)...),
		Preamble: preamble, Hide: hide,
		NoWrite:   append(l.MainRefFiles(cwd), l.RosterFiles()...),
		MemoryDir: memoryDir, Trusted: trusted, Chrome: team.Chrome(cfg),
		SandboxOff:  !l.Roster.SandboxOn(agent, cfg),
		MCPApproved: approved, ExtraDirs: extraDirs,
	})
	if v, ok := cfg["sandbox"].(bool); ok && !v && !l.Roster.SandboxOffGranted(agent) {
		l.warn("warning: agent '%s' has sandbox off in its agent.yaml, but the founder never turned it off (`hl agent set %s sandbox=off`, from a terminal) - ignoring it", agent, agent)
	}
	// The pair goes whole or not at all: a caller's own mode wins, and a lone value left behind would be read as the opening prompt.
	if len(mode) > 0 {
		own := false
		for _, x := range extra {
			if x == mode[0] || strings.HasPrefix(x, mode[0]+"=") {
				own = true
			}
		}
		if !own {
			argv = append(argv, mode...)
		}
	}
	if team.SkipPermissions(cfg) && !l.Roster.SkipPermissionsGranted(agent) {
		l.warn("warning: agent '%s' has skip_permissions in its agent.yaml, but the founder never turned it on (`hl agent register %s --skip-permissions on`, from a terminal) - ignoring it", agent, agent)
	} else if team.SkipPermissions(cfg) {
		flags := drv.SkipPermissionsArgv()
		if len(flags) == 0 {
			l.warn("warning: agent '%s' has skip_permissions on, but %s has no such switch - starting it as usual", agent, drv.Name())
		}
		for _, f := range flags {
			if !slices.Contains(extra, f) {
				argv = append(argv, f)
			}
		}
	}
	argv = append(argv, extra...)

	env := p.Accounts.EnvFor(acct, agent)
	if env == nil {
		env = map[string]string{}
	}
	for k, v := range drv.LaunchEnv(preamble, DisallowedTools, paths.AgentDir(l.Home, agent)) {
		env[k] = v
	}
	return argv, env, nil
}

func (l *Launcher) hiddenFolders() []string {
	if l.Tokens == nil {
		return nil
	}
	return l.Tokens.HiddenFolders()
}

// keepWhatItStartsWith keeps every session a launch starts (db.session_starts): why, whether it resumes the old conversation (then its
// id is the one after --resume), and what it is given. Bookkeeping never blocks a launch.
func (l *Launcher) keepWhatItStartsWith(agent string, extra []string, prompt, why, by string) error {
	resumed, sid := false, ""
	for i, x := range extra {
		if x == "--resume" {
			resumed = true
			if i+1 < len(extra) {
				sid = extra[i+1]
			}
			break
		}
	}
	text, err := db.GetHandoff(l.Q, agent)
	if err != nil {
		return err
	}
	if why == "" {
		why = "launch"
	}
	if _, err := db.NoteSessionStart(l.Q, agent, why, resumed, sid, text, prompt, by); err != nil {
		l.warn("hl launch: could not keep how %s was started: %v", agent, err)
	}
	return nil
}

// ExecAgent builds the launch and becomes the agent. It returns only when the launch is refused or the exec fails.
func (l *Launcher) ExecAgent(agent string, extra []string, force bool, promptFile, account, why, by string) error {
	argv, env, err := l.Build(agent, append([]string(nil), extra...), force, account)
	if err != nil {
		return err
	}
	prompt := TakePrompt(promptFile)
	if err := l.keepWhatItStartsWith(agent, extra, prompt, why, by); err != nil {
		return err // Python reads the waiting handoff outside its never-block try
	}
	if prompt != "" {
		drv, err := l.Ports.Vendors.ForAgent(agent)
		if err != nil {
			return err
		}
		argv = append(argv, drv.PromptArgv(prompt)...)
	}
	if promptFile == "" {
		// A launch nobody is waiting on consumed the handoff Build put in the system prompt. The daemon's own wake passes a prompt file and
		// clears the row itself once the session has started, because a launch that dies must leave it for the fallback launch.
		if err := db.SetHandoff(l.Q, agent, ""); err != nil {
			return err
		}
	}
	if l.Tokens != nil {
		l.Tokens.Record(agent, os.Getpid()) // the proof this session is the agent's, for the daemon's land verbs
		l.Tokens.MintKey(agent)             // what its hook alone can name, to vouch for its own `hl land`
	}
	env = paths.Both(env)
	fp, _ := l.Fingerprint(agent)
	l.recordTerminal(agent, env["OCP_ACCOUNT"], fp)
	gitrun.PointOriginHeadAtMain(l.cwd()) // the session names its main branch from it
	keys := make([]string, 0, len(env))
	for k := range env {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	flat := make([]string, 0, len(keys))
	for _, k := range keys {
		flat = append(flat, k+"="+env[k])
	}
	return l.Ports.Exec(argv[0], argv, flat)
}

// recordTerminal remembers where we are about to become the agent: the terminal, the account, and what Build produced this launch with.
// The host puts the handle in the environment of every terminal it launches, and this is the only moment it can be seen. A handle or
// worktree carrying a control byte is refused, not rewritten.
func (l *Launcher) recordTerminal(agent, acct, fp string) {
	var handle, wt string
	if l.Ports.TerminalHandle != nil {
		handle = l.Ports.TerminalHandle()
	}
	if l.Ports.WorktreeID != nil {
		wt = l.Ports.WorktreeID()
	}
	if handle != "" && textsafe.HasUnsafe(handle) {
		handle = ""
	}
	if wt != "" && textsafe.HasUnsafe(wt) {
		wt = ""
	}
	if handle == "" && acct == "" {
		return
	}
	err := func() error {
		if handle != "" {
			if err := db.SetTerminal(l.Q, agent, handle, wt); err != nil {
				return err
			}
			// However this terminal was made, the agent joins the team's Run here.
			if l.Ports.RegisterWorker != nil {
				if err := l.Ports.RegisterWorker(agent, handle); err != nil {
					return err
				}
			}
			rec, err := l.LaunchRecord(agent)
			if err != nil {
				return err
			}
			if err := db.SetLaunchFingerprint(l.Q, agent, fp, rec); err != nil {
				return err
			}
		}
		if acct != "" {
			return db.SetAccount(l.Q, agent, acct, "")
		}
		return nil
	}()
	if err != nil {
		// Not silent: with no fingerprint on record the daemon relaunches this session at every wake.
		l.warn("hl launch: could not record this launch for %s: %v", agent, err)
	}
}
