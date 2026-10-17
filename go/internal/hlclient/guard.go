package hlclient

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/handline/handline/go/internal/paths"
)

// LiveRefusal is why this hl may not run against its home, "" when it may (paths.home in Python): HANDLINE_HOME and
// OCP_HOME naming two homes; a test reaching the live home; or a checkout other than the one the installed hl runs
// reaching it - a worktree's hl run by hand - unless HANDLINE_ALLOW_LIVE=1 says it is meant.
func LiveRefusal(home string) string {
	newHome, oldHome := os.Getenv("HANDLINE_HOME"), os.Getenv("OCP_HOME")
	if strings.TrimSpace(newHome) != "" && strings.TrimSpace(oldHome) != "" && paths.Resolved(newHome) != paths.Resolved(oldHome) {
		return fmt.Sprintf("HANDLINE_HOME=%s and OCP_HOME=%s name different homes: set one, or both to the same folder", newHome, oldHome)
	}
	if !paths.IsLive(home) {
		return ""
	}
	if root, _ := paths.Getenv("TEST_ROOT"); root != "" {
		return "a test reached the real home: " + home
	}
	if allow, _ := paths.Getenv("ALLOW_LIVE"); allow == "1" {
		return ""
	}
	here, installed := checkouts()
	if installed == "" || here == installed {
		return ""
	}
	return fmt.Sprintf("%s is not the checkout the installed hl runs, and the home is the live one (%s): "+
		"point HANDLINE_HOME at a scratch folder, or set HANDLINE_ALLOW_LIVE=1 to mean it", here, home)
}

// checkouts is the checkout this binary is built in (it lives at <checkout>/go/hl) and the one the installed hl runs:
// $HANDLINE_BIN, else the first hl on PATH (<checkout>/bin/hl). installed is "" when nothing is installed.
func checkouts() (here, installed string) {
	if exe, err := os.Executable(); err == nil {
		here = filepath.Dir(filepath.Dir(paths.Resolved(exe)))
	}
	bin, _ := paths.Getenv("BIN")
	if bin == "" {
		for _, c := range paths.Commands {
			if p, err := exec.LookPath(c); err == nil {
				bin = p
				break
			}
		}
	}
	if bin != "" {
		installed = filepath.Dir(filepath.Dir(paths.Resolved(bin)))
	}
	return here, installed
}
