package compose

import (
	"strings"

	"github.com/handline/handline/go/internal/agentview"
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/heal"
	"github.com/handline/handline/go/internal/hooks"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/peerinbox"
	"github.com/handline/handline/go/internal/session"
	"github.com/handline/handline/go/internal/states"
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

// liveTerminals is the terminals the host lists, nil when it could not say (or there is no host).
func (p *Parts) liveTerminals() map[string]bool {
	if p.LC == nil || p.LC.term == nil {
		return nil
	}
	return p.LC.term.LiveTerminals()
}

// needsYou is where an agent's need of a person comes from, never the screen: the session's own file saying status waiting (read through
// terminalSession, the one reader of a terminal agent's session), and the wait file the PermissionRequest hook writes. An agent that is not
// there to wait (no row, closed, or no live session) needs nobody; a live session that says it is not waiting is past the prompt whatever a
// wait file still says, since the file is taken away only by the call that ran or a hook that ends the turn, and the call may run for
// minutes. The hook's file counts for an agent whose row is running (session.WaitOf): a session at a prompt sends no Stop, so its row stays
// running, and one that died there leaves the file behind. With Config.NotifiedNeedsYou, a prompt Claude Code's Notification hook reports
// as unanswered (hooks.Notified) needs a person too, at the terminal.
func (p *Parts) needsYou() func(agent string) *agentview.NeedsYou {
	home, q, notified := p.home, p.db, p.notified
	return func(agent string) *agentview.NeedsYou {
		row, err := db.GetAgentState(q, agent)
		if err != nil || row == nil || (row.ClosedAt.Valid && row.ClosedAt.Int64 != 0) {
			return nil
		}
		// The daemon's own record of an agent waiting on a person: a start-up dialog it will not answer, an agent it could not heal. One card
		// for the event, and it goes when the dialog closes or the trouble does.
		if row.State == states.Stalled && strings.Contains(row.Detail.String, lifecycle.DialogStall) {
			return &agentview.NeedsYou{Kind: "dialog", Source: "daemon", Since: row.Updated, Text: row.Detail.String}
		}
		if since, up := heal.CardUp(q, agent); up {
			return &agentview.NeedsYou{Kind: "stuck", Source: "daemon", Since: since, Text: heal.CardText}
		}
		var sess *sessionRec
		if rec, _, found := p.terminalSession(agent, p.liveTerminals()); found == sessionFound {
			sess = &rec
		}
		if sess != nil && sess.Status != "waiting" {
			return nil
		}
		w := hooks.ReadWait(home, agent)
		// Claude Code saying nobody answered comes first; a question is left to its own card.
		if since, ok := hooks.Notified(home, agent); notified && ok && w["tool"] != "AskUserQuestion" {
			return &agentview.NeedsYou{Kind: "permission", Source: "notification", Since: since, Text: "handle this in the terminal"}
		}
		if w != nil && row.State == states.Running && !movedOn(q, agent, w) {
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
			s := peerinbox.Session{Status: sess.Status, WaitingFor: sess.WaitingFor}
			return &agentview.NeedsYou{Kind: s.NeedsKind(), Source: "session", Since: sess.UpdatedAt / 1000, Text: sess.WaitingFor}
		}
		return nil
	}
}
