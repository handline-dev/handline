package term

import (
	"strings"
)

// ShellOption is the tmux pane option that names a plain shell the daemon opened (terminal.new); a pane without it is not a shell.
const ShellOption = "@hl_shell"

// Shell is a plain shell's pane: its handle, its name, the folder it is in now and what runs in it now.
type Shell struct{ Handle, Name, Cwd, Command string }

// Shells opens and lists plain shells: windows with no agent, kept by tmux across restarts of the app and the daemon.
type Shells interface {
	// CreateShell opens a shell window in cwd stamped with name and answers its handle, or why not.
	CreateShell(cwd, name string) (handle, why string)
	// Shells are the shell panes the host has now; ok false when it cannot say.
	Shells() (shells []Shell, ok bool)
}

func (t Tmux) CreateShell(cwd, name string) (string, string) {
	pane, why := t.CreateTerminal("", cwd, name, "")
	if pane == "" {
		return "", why
	}
	if code, _, err := t.tmux(tmuxTimeout, "", "set-option", "-p", "-t", pane, ShellOption, name); code != 0 {
		t.CloseTerminal(pane, true)
		return "", "could not stamp the new window as a shell: " + err
	}
	return pane, ""
}

func (t Tmux) Shells() ([]Shell, bool) {
	code, out, _ := t.tmux(tmuxTimeout, "", "list-panes", "-a", "-F", "#{pane_id}\t#{"+ShellOption+"}\t#{pane_current_path}\t#{pane_current_command}")
	if code != 0 {
		return nil, t.ListTerminals() != nil // no server is no shells
	}
	var shells []Shell
	for _, line := range strings.Split(out, "\n") {
		f := strings.Split(line, "\t")
		if len(f) == 4 && paneRE.MatchString(f[0]) && f[1] != "" {
			shells = append(shells, Shell{Handle: f[0], Name: f[1], Cwd: f[2], Command: f[3]})
		}
	}
	return shells, true
}

func (d dynamic) CreateShell(cwd, name string) (string, string) {
	if h, ok := d.cur().(Shells); ok {
		return h.CreateShell(cwd, name)
	}
	return "", "the " + d.Name() + " terminal host cannot open shells"
}

func (d dynamic) Shells() ([]Shell, bool) {
	if h, ok := d.cur().(Shells); ok {
		return h.Shells()
	}
	return nil, false
}

// shellKeyPrefix keys a shell where a terminal's owner is an agent's name: its input holder, for one.
const shellKeyPrefix = "shell:"

// ShellKey is the name a shell's input hold is kept under.
func ShellKey(handle string) string { return shellKeyPrefix + handle }

// ShellHandle is the handle a ShellKey names; ok false for an agent's name.
func ShellHandle(key string) (string, bool) { return strings.CutPrefix(key, shellKeyPrefix) }

// ShellOf is the shell with this handle among shells; nil when it is not one.
func ShellOf(shells []Shell, handle string) *Shell {
	for i := range shells {
		if shells[i].Handle == handle {
			return &shells[i]
		}
	}
	return nil
}
