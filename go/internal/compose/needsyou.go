package compose

import (
	"strings"
	"syscall"

	"github.com/handline/handline/go/internal/agentview"
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/hooks"
	"github.com/handline/handline/go/internal/peerinbox"
	"github.com/handline/handline/go/internal/session"
	"github.com/handline/handline/go/internal/states"
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

// needsYou is where an agent's need of a person comes from, never the screen: the session's own file saying status waiting, and the wait
// file the PermissionRequest hook writes. An agent that is not there to wait (no row, closed, or no live session) needs nobody; a live
// session that says it is not waiting is past the prompt whatever a wait file still says, since the file is taken away only by the call
// that ran or a hook that ends the turn, and the call may run for minutes. The hook's file counts for an agent whose row is running
// (session.WaitOf): a session at a prompt sends no Stop, so its row stays running, and one that died there leaves the file behind.
func needsYou(home string, q db.Q) func(agent string) *agentview.NeedsYou {
	return func(agent string) *agentview.NeedsYou {
		row, err := db.GetAgentState(q, agent)
		if err != nil || row == nil || (row.ClosedAt.Valid && row.ClosedAt.Int64 != 0) {
			return nil
		}
		var sess *peerinbox.Session
		if row.Account.Valid && row.Worktree.Valid && row.Worktree.String != "" {
			if config, err := team.AccountConfigDir(home, row.Account.String); err == nil {
				if s, err := peerinbox.ByCwd(config, row.Worktree.String, func(pid int) bool { return syscall.Kill(pid, 0) == nil }); err == nil {
					sess = &s
				}
			}
		}
		if sess != nil && !sess.Blocked() {
			return nil
		}
		if w := hooks.ReadWait(home, agent); w != nil && row.State == states.Running && !movedOn(q, agent, w) {
			since, _ := w["since"].(float64)
			tool, _ := w["tool"].(string)
			command, _ := w["command"].(string)
			kind := "permission"
			if tool == "AskUserQuestion" {
				kind = "question"
			}
			return &agentview.NeedsYou{Kind: kind, Source: "hook", Since: int64(since), Text: strings.TrimSpace(tool + ": " + command)}
		}
		if sess != nil {
			return &agentview.NeedsYou{Kind: sess.NeedsKind(), Source: "session", Since: sess.UpdatedAt / 1000, Text: sess.WaitingFor}
		}
		return nil
	}
}
