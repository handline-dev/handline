package termchan

import (
	"os"
	"path/filepath"
	"strings"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/term"
	"github.com/handline/handline/go/internal/textsafe"
)

// Plain shells (terminal.new, terminal.shells): tmux windows with no agent, watched and typed into like an agent's terminal. tmux keeps
// them, so they outlive the app and the daemon, and the list is read from it each time.

const maxShellName = 60

func (e *Env) shellHost() (term.Shells, error) {
	h, ok := e.Backend().(term.Shells)
	if !ok || !e.Backend().Available() {
		return nil, rpc.Refused("the terminal host ("+e.Backend().Name()+") cannot open shells", "unsupported", "")
	}
	return h, nil
}

func shellOut(s term.Shell) map[string]any {
	return map[string]any{"terminal": s.Handle, "name": s.Name, "cwd": s.Cwd, "command": s.Command}
}

// newShell is terminal.new: a plain shell in cwd (the person's home when not given), named name (the folder's when not given).
func (e *Env) newShell(_ *rpc.Conn, params map[string]any) (any, error) {
	cwd, _ := params["cwd"].(string)
	if v, has := params["cwd"]; has && (cwd == "" || v == nil) {
		return nil, bad("cwd is a folder", "cwd")
	}
	if cwd == "" {
		cwd, _ = os.UserHomeDir()
	}
	if !filepath.IsAbs(cwd) {
		return nil, bad("cwd is an absolute path", "cwd")
	}
	if st, err := os.Stat(cwd); err != nil || !st.IsDir() {
		return nil, bad("cwd is not a folder on this machine: "+cwd, "cwd")
	}
	name, _ := params["name"].(string)
	if v, has := params["name"]; has && v != nil && name == "" {
		return nil, bad("name is text", "name")
	}
	name = strings.TrimSpace(textsafe.Oneline(name))
	if name == "" {
		name = filepath.Base(cwd)
	}
	if r := []rune(name); len(r) > maxShellName {
		name = string(r[:maxShellName])
	}
	h, err := e.shellHost()
	if err != nil {
		return nil, err
	}
	handle, why := h.CreateShell(cwd, name)
	if handle == "" {
		return nil, rpc.Refused("the shell could not be opened: "+why, "state", "")
	}
	return map[string]any{"accepted": true, "said": "shell " + name + " opened in " + cwd, "terminal": handle, "name": name, "cwd": cwd}, nil
}

// shells is terminal.shells: the plain shells open now.
func (e *Env) shells(_ *rpc.Conn, _ map[string]any) (any, error) {
	h, err := e.shellHost()
	if err != nil {
		return nil, err
	}
	list, ok := h.Shells()
	if !ok {
		return nil, rpc.Refused("the terminal host could not be asked", "state", "")
	}
	out := []any{}
	for _, s := range list {
		out = append(out, shellOut(s))
	}
	return map[string]any{"shells": out}, nil
}
