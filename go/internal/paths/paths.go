// Package paths is the layout of the handline home, as handline/infra/paths.py has it.
package paths

import (
	"os"
	"path/filepath"
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
