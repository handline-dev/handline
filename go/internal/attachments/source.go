package attachments

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/housekeeping"
	"github.com/handline/handline/go/internal/paths"
)

// Roots are where an agent's named files may be read from (nil: anywhere, for the founder), since the daemon reads outside its sandbox.
func Roots(q db.Q, home, agent string, founder bool) ([]string, error) {
	if founder {
		return nil, nil
	}
	roots := []string{paths.AgentDir(home, agent), housekeeping.ScratchRoot()}
	st, err := db.GetAgentState(q, agent)
	if err != nil {
		return nil, err
	}
	if st != nil && st.Worktree.String != "" {
		wt := st.Worktree.String
		if i := strings.Index(wt, "::"); i >= 0 { // the column may hold `<repo>::<path>`
			wt = wt[i+2:]
		}
		roots = append(roots, wt)
	}
	return roots, nil
}

// under resolves links on both sides, so a link cannot lead outside a root.
func under(path string, roots []string) bool {
	real, err := filepath.EvalSymlinks(path)
	if err != nil {
		return false
	}
	for _, r := range roots {
		if root, err := filepath.EvalSymlinks(r); err == nil && strings.HasPrefix(real, root+string(filepath.Separator)) {
			return true
		}
	}
	return false
}

// ReadUnder refuses a relative path, since the daemon's working folder is not the agent's.
func ReadUnder(p string, roots []string, max int64, what, field string) ([]byte, error) {
	if !filepath.IsAbs(p) {
		return nil, badParam("a "+what+" is named by its absolute path: "+p, field)
	}
	if roots != nil && !under(p, roots) {
		return nil, badParam(p+" is outside the places a "+what+" may be taken from: the agent's worktree, its folder and the scratch folder", field)
	}
	f, err := os.Open(p)
	if err != nil {
		return nil, badParam("cannot read the "+what+" "+p, field)
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil || !st.Mode().IsRegular() {
		return nil, badParam(p+" is not a file", field)
	}
	if st.Size() > max {
		return nil, badParam(fmt.Sprintf("%s is over %d MiB", p, max>>20), field)
	}
	b, err := io.ReadAll(io.LimitReader(f, max+1))
	if err != nil || int64(len(b)) > max {
		return nil, badParam("cannot read the "+what+" "+p, field)
	}
	return b, nil
}
