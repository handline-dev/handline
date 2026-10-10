package compose

import (
	"strings"
	"syscall"

	"github.com/handline/handline/go/internal/agentview"
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/hooks"
	"github.com/handline/handline/go/internal/peerinbox"
	"github.com/handline/handline/go/internal/session"
	"github.com/handline/handline/go/internal/team"
)

// movedOn is whether the agent's own turn went on past the prompt the wait file names (session.WaitOf's second path): it asked, reported or
// committed after the prompt was raised, so the prompt was answered. A wait that names no event is left showing rather than guessed at.
func movedOn(q db.Q, agent string, w map[string]any) bool {
	after, ok := hooks.WaitID(w)
	if !ok {
		return false
	}
	last, found, err := db.LastActivityID(q, agent, session.MovedOnKinds)
	return err == nil && found && last > after
}

// needsYou is where an agent's need of a person comes from: the wait file the PermissionRequest hook writes (and the next hook of the
// turn takes away), else the session's own file saying status waiting. Never the screen.
func needsYou(home string, q db.Q) func(agent string) *agentview.NeedsYou {
	return func(agent string) *agentview.NeedsYou {
		if w := hooks.ReadWait(home, agent); w != nil && !movedOn(q, agent, w) {
			since, _ := w["since"].(float64)
			tool, _ := w["tool"].(string)
			command, _ := w["command"].(string)
			kind := "permission"
			if tool == "AskUserQuestion" {
				kind = "question"
			}
			return &agentview.NeedsYou{Kind: kind, Source: "hook", Since: int64(since), Text: strings.TrimSpace(tool + ": " + command)}
		}
		row, err := db.GetAgentState(q, agent)
		if err != nil || row == nil || !row.Account.Valid || !row.Worktree.Valid || row.Worktree.String == "" {
			return nil
		}
		config, err := team.AccountConfigDir(home, row.Account.String)
		if err != nil {
			return nil
		}
		s, err := peerinbox.ByCwd(config, row.Worktree.String, func(pid int) bool { return syscall.Kill(pid, 0) == nil })
		if err != nil || !s.Blocked() {
			return nil
		}
		return &agentview.NeedsYou{Kind: s.NeedsKind(), Source: "session", Since: s.UpdatedAt / 1000, Text: s.WaitingFor}
	}
}
