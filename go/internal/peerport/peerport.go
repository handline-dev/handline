// Package peerport is the supervisor's Agents port for a terminal agent: a message goes in through the session's peer inbox
// (package peerinbox), never as keystrokes. The supervisor's one send path (ledger, wake, tell, send now) is the structured agents'; this
// is the other implementation of what it asks of a session.
//
// A write is not an echo. Send answers Sent at once and delivers in the background, one message at a time per agent and in order; the
// outcome comes back through OnDelivered (the transcript showed it) or OnFailed (it did not after the resend: the agent needs a person).
// The receiver queues a message that arrives mid-turn and reads it when the turn ends, so Now and Queue are the same write.
package peerport

import (
	"context"
	"errors"
	"sync"

	"github.com/handline/handline/go/internal/peerinbox"
	"github.com/handline/handline/go/internal/structured"
)

// Deliverer is what the port needs of the inbox: *peerinbox.Sender.
type Deliverer interface {
	Deliver(ctx context.Context, sess peerinbox.Session, text, fromMode string) peerinbox.Result
}

// Port delivers to terminal agents.
type Port struct {
	// Peer says whether agent is delivered to through its inbox.
	Peer func(agent string) bool
	// Find is the agent's live session record.
	Find   func(agent string) (peerinbox.Session, error)
	Sender Deliverer
	// OnDelivered is called once the transcript shows op. OnFailed is called when it did not, after the resend, or the session has no
	// inbox any more; why carries nothing from the connection or the key, and written says whether the message was written at all.
	OnDelivered func(agent, op string)
	OnFailed    func(agent, op, why string, written bool)
	Ctx         context.Context
	// Mode is the permission class (peerinbox.Bypass or Prompting) attested for the message op to agent; "" attests none.
	Mode func(agent, op string) string

	mu    sync.Mutex
	lines map[string]*line
}

type job struct{ op, text, mode string }

type line struct {
	jobs    []job
	running bool
}

// Structured says whether agent is delivered to by the send path: here, whether it uses its inbox.
func (p *Port) Structured(agent string) bool { return p.Peer != nil && p.Peer(agent) }

// Live says whether the agent's session has an inbox that can be written to.
func (p *Port) Live(agent string) bool {
	_, err := p.Find(agent)
	return err == nil
}

// Send takes the message and delivers it in the background; that an op goes out once is the ledger's (supervisor sendOnce). An error means nothing was written.
func (p *Port) Send(agent, op, text string, _ structured.Delivery) (structured.Receipt, error) {
	if _, err := p.Find(agent); err != nil {
		return structured.Receipt{}, errors.New(agent + " has no live session inbox: " + err.Error())
	}
	mode := ""
	if p.Mode != nil {
		mode = p.Mode(agent, op)
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.lines == nil {
		p.lines = map[string]*line{}
	}
	l := p.lines[agent]
	if l == nil {
		l = &line{}
		p.lines[agent] = l
	}
	l.jobs = append(l.jobs, job{op, text, mode})
	if !l.running {
		l.running = true
		go p.drain(agent, l)
	}
	return structured.Receipt{Op: op, State: structured.Sent}, nil
}

// Withdraw cannot take back a message the inbox has taken: it may already be written.
func (p *Port) Withdraw(_, op string) (structured.Receipt, bool, error) {
	return structured.Receipt{Op: op, State: structured.Sent}, false, nil
}

// Promote has nothing to promote: a message is written as soon as its turn in the agent's line comes.
func (p *Port) Promote(_, op string) (structured.Receipt, error) {
	return structured.Receipt{Op: op, State: structured.Sent}, nil
}

func (p *Port) drain(agent string, l *line) {
	ctx := p.Ctx
	if ctx == nil {
		ctx = context.Background()
	}
	for {
		p.mu.Lock()
		if len(l.jobs) == 0 {
			l.running = false
			p.mu.Unlock()
			return
		}
		j := l.jobs[0]
		l.jobs = l.jobs[1:]
		p.mu.Unlock()
		p.deliver(ctx, agent, j)
	}
}

func (p *Port) deliver(ctx context.Context, agent string, j job) {
	sess, err := p.Find(agent)
	if err != nil {
		p.failed(agent, j.op, err.Error(), false)
		return
	}
	res := p.Sender.Deliver(ctx, sess, j.text, j.mode)
	if res.Kind == peerinbox.Delivered {
		if p.OnDelivered != nil {
			p.OnDelivered(agent, j.op)
		}
		return
	}
	p.failed(agent, j.op, res.Why, res.Kind != peerinbox.NoSocket)
}

func (p *Port) failed(agent, op, why string, written bool) {
	if p.OnFailed != nil {
		p.OnFailed(agent, op, why, written)
	}
}
