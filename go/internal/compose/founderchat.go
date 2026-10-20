package compose

import (
	"errors"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/inputhold"
	"github.com/handline/handline/go/internal/lock"
	"github.com/handline/handline/go/internal/sessionrpc"
	"github.com/handline/handline/go/internal/turns"
)

// typedOps is the part of the telling that says which messages are the founder's typed words (supervisor.TypedOp).
type typedOps interface {
	TypedOp(q db.Q, agent, op string) bool
}

func (p *Parts) typedOp(agent, op string) bool {
	t, ok := p.tell.(typedOps)
	return ok && p.db != nil && t.TypedOp(p.db, agent, op)
}

// typeable is "" when the founder's words may be typed at agent now, else why not: its hooks must have said where its turn is (idle, or
// mid-turn, where the line is queued into the turn), no person or device may hold its input, nothing on its screen may be what a typed
// line would answer, and no shell may be left in place of its session (term.ShellLeft).
func (p *Parts) typeable(agent string) string {
	if p.turns == nil || p.turns.State(agent) == turns.Unknown {
		return "its turn is not known from its hooks yet"
	}
	if p.LC == nil || p.LC.term == nil || p.db == nil {
		return "no terminal host"
	}
	handle, err := db.Terminal(p.db, agent)
	if err != nil || handle == "" {
		return "it has no terminal"
	}
	if held, err := (&inputhold.Store{DB: p.db}).Held(agent); err != nil || held {
		return "a person or device holds its input"
	}
	if b := p.LC.term.ScreenBlocker(handle); b != nil {
		return b.Reason()
	}
	return p.LC.term.ShellLeft(handle)
}

// typeIn types the founder's words at agent and presses Enter, under the terminal lock a person typing there holds too.
func (p *Parts) typeIn(agent, text string) error {
	hold := lock.TakeTerminal(p.home, agent)
	if hold == nil {
		return errors.New(sessionrpc.TypingElsewhere)
	}
	defer hold.Release()
	if why := p.typeable(agent); why != "" {
		return errors.New(why)
	}
	handle, err := db.Terminal(p.db, agent)
	if err != nil {
		return err
	}
	if ok, why := p.LC.term.Send(handle, text, true); !ok {
		return errors.New(why)
	}
	return nil
}
