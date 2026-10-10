// Package claude is the Claude Code vendor as a launch uses it (handline/vendors/claude.py and claude_hooks.py): the
// command line a terminal agent is started with, and the shared settings file that carries ocp's hooks.
package claude

import (
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/handline/handline/go/internal/lifecycle"
)

// Driver is the Claude vendor for one home.
type Driver struct{ Home string }

var _ lifecycle.Driver = Driver{}

func (Driver) Name() string { return "claude" }

// IsResume is whether the extra arguments resume a session, in any of its three spellings.
func (Driver) IsResume(extra []string) bool {
	for _, x := range extra {
		if x == "--resume" || strings.HasPrefix(x, "--resume=") || x == "-r" {
			return true
		}
	}
	return false
}

// Binary is the claude on PATH, "" when there is none.
func (Driver) Binary() string {
	p, err := exec.LookPath("claude")
	if err != nil {
		return ""
	}
	return p
}

func (d Driver) Hooks() lifecycle.Hooks { return Hooks{Home: d.Home} }

func (Driver) AutoModeArgv() []string        { return []string{"--permission-mode", "auto"} }
func (Driver) SkipPermissionsArgv() []string { return []string{"--dangerously-skip-permissions"} }

func (Driver) PermissionModeArgv(mode string) []string {
	switch mode {
	case "default", "acceptEdits", "plan":
		return []string{"--permission-mode", mode}
	}
	return nil
}

func (Driver) LaunchEnv(string, string, string) map[string]string { return map[string]string{} }
func (Driver) PromptArgv(prompt string) []string                  { return []string{prompt} }

var notAlnum = regexp.MustCompile(`[^A-Za-z0-9]`)

// TranscriptDir is where the sessions run in worktree are recorded: claude names the folder after the path, every
// character that is not a letter or digit becoming a dash.
func (Driver) TranscriptDir(configDir, worktree string) (string, bool) {
	return filepath.Join(configDir, "projects", notAlnum.ReplaceAllString(worktree, "-")), true
}

// LaunchArgv is the command line a session starts with. Every setting goes in one --settings object, since a second
// one would replace the first.
func (Driver) LaunchArgv(exe string, o lifecycle.LaunchArgs) []string {
	argv := []string{exe}
	if o.Model != "" {
		argv = append(argv, "--model", o.Model)
	}
	argv = append(argv, "--disallowedTools", o.Disallowed, "--effort", o.Effort)
	if o.Chrome {
		argv = append(argv, "--chrome")
	} else {
		argv = append(argv, "--no-chrome")
	}
	argv = append(argv, o.MCPArgs...)
	if len(o.ExtraDirs) > 0 {
		argv = append(append(argv, "--add-dir"), o.ExtraDirs...)
	}
	if o.Preamble != "" {
		argv = append(argv, "--append-system-prompt", o.Preamble)
	}
	settings := NewObject()
	if o.MemoryDir != "" {
		settings.Set("autoMemoryDirectory", o.MemoryDir)
	}
	if len(o.Hide) > 0 || len(o.NoWrite) > 0 || len(o.ExtraDirs) > 0 {
		fs := NewObject()
		if len(o.Hide) > 0 {
			fs.Set("denyRead", o.Hide)
		}
		if len(o.NoWrite) > 0 {
			fs.Set("denyWrite", o.NoWrite)
		}
		if len(o.ExtraDirs) > 0 {
			fs.Set("allowWrite", o.ExtraDirs) // a working directory is readable; Bash may write it only when it is named here
		}
		sb := NewObject()
		sb.Set("filesystem", fs)
		settings.Set("sandbox", sb)
	}
	// Said either way, so the shared settings never decide it. Off, commands run unsandboxed; the Read/Edit denies below
	// are permissions, not the sandbox, and stay.
	settings.setDefault("sandbox", NewObject()).(*Object).Set("enabled", !o.SandboxOff)
	var deny []string
	for _, p := range o.Hide {
		deny = append(deny, "Read(/"+strings.TrimRight(p, "/")+"/**)") // denyRead binds Bash; the Read tool is a permission
	}
	for _, p := range o.NoWrite {
		deny = append(deny, "Edit(/"+p+")") // Claude Code matches only Edit rules for the file tools, and they cover Write
	}
	if len(deny) > 0 {
		perms := NewObject()
		perms.Set("deny", deny)
		settings.Set("permissions", perms)
	}
	if len(o.Trusted) > 0 {
		// The classifier reads autoMode from --settings; "$defaults" keeps the built-in entries.
		auto := NewObject()
		auto.Set("environment", append([]string{"$defaults"}, o.Trusted...))
		settings.Set("autoMode", auto)
	}
	if len(o.MCPApproved) > 0 {
		settings.Set("enabledMcpjsonServers", o.MCPApproved) // else the session waits at start for a person to approve them
	}
	if settings.Len() > 0 {
		argv = append(argv, "--settings", Dumps(settings, 0, true))
	}
	return argv
}
