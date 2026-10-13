// Package paths is the layout of the handline home, as handline/infra/paths.py has it.
package paths

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
)

// Getenv reads name under both prefixes, HANDLINE_ first, as infra/names.getenv does.
func Getenv(suffix string) (string, bool) {
	for _, p := range []string{"HANDLINE_", "OCP_"} {
		if v, ok := os.LookupEnv(p + suffix); ok {
			return v, true
		}
	}
	return "", false
}

// UserHome is the user's home, or the test suite's stand-in for it.
func UserHome() string {
	if t, ok := Getenv("TEST_ROOT"); ok && t != "" {
		return t
	}
	h, _ := os.UserHomeDir()
	return h
}

// Home is the handline home: the environment's, else ~/.handline once it exists, else ~/.orcapipe.
func Home() string {
	if h, ok := Getenv("HOME"); ok && h != "" {
		return h
	}
	if st, err := os.Stat(filepath.Join(UserHome(), ".handline")); err == nil && st.IsDir() {
		return filepath.Join(UserHome(), ".handline")
	}
	return filepath.Join(UserHome(), ".orcapipe")
}

func DBFile(home string) string     { return filepath.Join(home, "db.sqlite") }
func SocketFile(home string) string { return filepath.Join(home, "daemon.sock") }

// Commands is the command, main name first (handline/infra/names.py).
var Commands = []string{"hl", "handline", "ocp"}

// Setenv sets suffix in both spellings in env, so whichever a reader tries first reads this value.
func Setenv(env map[string]string, suffix, value string) {
	env["OCP_"+suffix] = value
	env["HANDLINE_"+suffix] = value
}

// Both is env with a HANDLINE_ twin for every OCP_ entry and an OCP_ twin for every HANDLINE_ one, each taking the
// other's value only when it has none of its own.
func Both(env map[string]string) map[string]string {
	out := make(map[string]string, len(env)*2)
	for k, v := range env {
		out[k] = v
	}
	for k, v := range env {
		if rest, ok := strings.CutPrefix(k, "OCP_"); ok {
			if _, has := out["HANDLINE_"+rest]; !has {
				out["HANDLINE_"+rest] = v
			}
		} else if rest, ok := strings.CutPrefix(k, "HANDLINE_"); ok {
			if _, has := out["OCP_"+rest]; !has {
				out["OCP_"+rest] = v
			}
		}
	}
	return out
}

func ConfigFile(home string) string  { return filepath.Join(home, "config.yaml") }
func TeamFile(home string) string    { return filepath.Join(home, "team.yaml") }
func RelaySocket(home string) string { return filepath.Join(home, "relay.sock") }
func GatewaySocket(home string) string {
	return filepath.Join(home, "gateway.sock")
}
func MCPConfig(home string) string        { return filepath.Join(home, "shared", "mcp.json") }
func AccountsDir(home string) string      { return filepath.Join(home, "accounts") }
func AccountDir(home, acct string) string { return filepath.Join(home, "accounts", acct) }
func StatusFile(home, acct string) string {
	return filepath.Join(AccountDir(home, acct), "status.json")
}
func AgentsDir(home string) string { return filepath.Join(home, "agents") }
func AgentDir(home, name string) string {
	return filepath.Join(home, "agents", name)
}
func AgentStatusFile(home, name string) string {
	return filepath.Join(AgentDir(home, name), "status.json")
}
func HandoffNoteFile(home, agent string) string {
	return filepath.Join(AgentDir(home, agent), "handoff.md")
}

// StrayHandoffFile is where a session in a dangling terminal of the agent writes its handoff before the terminal is closed.
func StrayHandoffFile(home, agent string, pid int) string {
	return filepath.Join(AgentDir(home, agent), fmt.Sprintf("handoff-%d.md", pid))
}

// ListAccounts is the account folders, by name, not hidden ones.
func ListAccounts(home string) []string {
	entries, err := os.ReadDir(AccountsDir(home))
	if err != nil {
		return []string{}
	}
	out := []string{}
	for _, e := range entries {
		if st, err := os.Stat(filepath.Join(AccountsDir(home), e.Name())); err == nil && st.IsDir() && !strings.HasPrefix(e.Name(), ".") {
			out = append(out, e.Name())
		}
	}
	sort.Strings(out)
	return out
}

// Removing is whether the account is mid-removal: nothing is started on an account about to go.
func Removing(home, acct string) bool {
	_, err := os.Stat(filepath.Join(AccountDir(home, acct), ".removing"))
	return err == nil
}

// ListAgents is the registered agents: a folder holding agent.yaml, by name. A listing that cannot be read is an empty one; a caller
// that must tell the two apart uses ReadAgents.
func ListAgents(home string) []string {
	out, _ := ReadAgents(home)
	if out == nil {
		return []string{}
	}
	return out
}

// ReadAgents is ListAgents with the read errors: the agents directory, or an entry's agent.yaml, that cannot be read for a reason other
// than not being there (no agents directory is no agents).
func ReadAgents(home string) ([]string, error) {
	entries, err := os.ReadDir(AgentsDir(home))
	if errors.Is(err, fs.ErrNotExist) {
		return []string{}, nil
	}
	if err != nil {
		return nil, err
	}
	out := []string{}
	for _, e := range entries {
		_, err := os.Stat(filepath.Join(AgentsDir(home), e.Name(), "agent.yaml"))
		if err == nil {
			out = append(out, e.Name())
		} else if !errors.Is(err, fs.ErrNotExist) && !errors.Is(err, syscall.ENOTDIR) {
			return nil, err
		}
	}
	sort.Strings(out)
	return out, nil
}

// OcpExe is the command a hook or launch line should call hl with: HANDLINE_BIN or OCP_BIN, else the first of Commands on PATH.
func OcpExe() string {
	if v, ok := Getenv("BIN"); ok && v != "" {
		return v
	}
	for _, c := range Commands {
		if p, err := exec.LookPath(c); err == nil {
			return p
		}
	}
	return ""
}
