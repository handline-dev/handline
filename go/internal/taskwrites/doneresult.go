package taskwrites

import (
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/gitrun"
	"github.com/handline/handline/go/internal/housekeeping"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/taskresult"
)

// resultParams are task.done's commit and shots.
func resultParams(p map[string]any) (commit string, shots []string, err error) {
	str := func(key string) (string, error) {
		if p[key] == nil {
			return "", nil
		}
		s, ok := p[key].(string)
		if !ok || strings.TrimSpace(s) == "" {
			return "", badParam(key+" is text and not empty", key)
		}
		return strings.TrimSpace(s), nil
	}
	if commit, err = str("commit"); err != nil {
		return
	}
	if commit != "" && !shaLike.MatchString(commit) {
		return "", nil, badParam("commit is a sha", "commit")
	}
	if v := p["shots"]; v != nil {
		list, ok := v.([]any)
		if !ok || len(list) == 0 {
			return "", nil, badParam("shots is a list of PNG file paths", "shots")
		}
		for _, x := range list {
			s, ok := x.(string)
			if !ok || s == "" {
				return "", nil, badParam("shots is a list of PNG file paths", "shots")
			}
			shots = append(shots, s)
		}
	}
	return
}

// doneResult is what the end of task t keeps beyond its evidence: the commit (checked to exist in the repo, and whether main reaches it),
// and the screenshots, copied into the daemon's folder. Neither is required. Nothing is written but the screenshots' copies; the caller
// records the result.
func doneResult(tx db.Q, home, by string, t *task, in DoneIn, via, mark string) (res taskresult.Result, finalize func() error, err error) {
	switch {
	case in.Commit != "":
		full, onMain, why := commitIn(in.Repo, in.Commit)
		if full == "" {
			return res, nil, refusal(fmt.Sprintf("%s is not a commit in this repo (%s)", in.Commit, why))
		}
		res.Commit, res.OnMain = full, onMain
	case via == "sha":
		res.Commit, res.OnMain = mark, true
	}
	if len(in.Shots) > 0 {
		var roots []string // the founder may name any file; an agent only its own places, since the daemon reads it outside the agent's sandbox
		if by != human {
			if roots, err = shotRoots(tx, home, by); err != nil {
				return res, nil, err
			}
		}
		if res.Shots, finalize, err = taskresult.Stage(home, t.ID, in.Shots, roots); err != nil {
			return res, nil, err
		}
	}
	return res, finalize, nil
}

// shotRoots are the folders an agent's screenshots may be read from: its worktree, its folder under the home and the scratch folder.
func shotRoots(q db.Q, home, agent string) ([]string, error) {
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

// commitIn is the full sha that sha names in repo, and whether main reaches it; "" and why when it names no commit.
func commitIn(repo, sha string) (full string, onMain bool, why string) {
	r := gitrun.Run(repo, "rev-parse", "--verify", "--quiet", sha+"^{commit}")
	if r.RC != 0 {
		if r.RC < 0 {
			return "", false, r.Stderr
		}
		return "", false, "no such commit"
	}
	full = strings.TrimSpace(r.Stdout)
	return full, gitrun.Run(repo, "merge-base", "--is-ancestor", full, gitrun.IntegrationBranch).RC == 0, ""
}
