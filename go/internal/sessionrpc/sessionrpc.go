// Package sessionrpc is the methods that drive an agent's session through the daemon: session.start, wake, stop, restart, park and
// interrupt, and terminal.close (handline/daemon/rpc_session.py, rpc_typing.py's interrupt).
//
// The session methods are thin: validation, the verbs' own state checks, and the request_id effect, over the lifecycle's Controls
// (structured sessions, S3). A success is recorded under the caller's request_id the moment the action returns, so a retry after a crash
// that lost the reply replays it instead of acting again. A refusal names its gate: terminal_lock when whoever types at a terminal agent
// holds the lock the tick takes, state for every other reason the action gives.
//
// session.input, keys and type and the input holder (typing.go) serve a terminal agent; a structured session has no terminal to hold and
// chat.send reaches it. Dropped with the design: the exit mode that typed /exit. session.interrupt is a control request to the CLI, not Esc. A method whose port is
// nil is not registered, so hello never lists what cannot be served.
package sessionrpc

import (
	"database/sql"
	"fmt"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/inputhold"
	"github.com/handline/handline/go/internal/lock"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/term"
)

// TypingElsewhere is the refusal's reason while whoever types at a terminal agent holds the lock the tick takes.
const TypingElsewhere = "someone else is typing at this agent"

// Call is who is acting, for the lifecycle.
type Call struct {
	Person bool
	By     string // the caller's actor name
}

// Controls is actions/control.py and daemon/lifecycle.py as the session methods use them (the S3 lifecycle). Each returns whether it did,
// and what it says; the words of a refusal are the verb's own.
type Controls interface {
	// Start is control.start_result: give an agent a worktree and start it there. repo "" is the working directory; base is never "".
	Start(c Call, agent, repo, base string) (ok bool, said string, err error)
	// Wake is lifecycle.wake_agent; why is the lifecycle's own words, not yet prefixed with the agent.
	Wake(c Call, agent string) (ok bool, why string, err error)
	// Close is control.close_result, fresh also dropping the session id.
	Close(c Call, agent string, fresh bool) (ok bool, said string, err error)
	// Restart is control.restart_result.
	Restart(c Call, agent string, fresh, force bool) (ok bool, said string, err error)
	// Park is control.park_result: record a handoff, park until the window rolls over.
	Park(c Call, agent string) (ok bool, said string, err error)
}

// Procs is the running structured sessions: which agents have one, a turn interrupted by control request, and the process group ended
// at once. Kill returns once the group is gone, or an error.
type Procs interface {
	Structured(agent string) bool
	Interrupt(agent string) error
	Kill(agent string) error
}

// Env is what the methods run on.
type Env struct {
	DB       *sql.DB
	Home     string
	Controls Controls
	Procs    Procs
	// Terminal agents only (frozen): the terminal's close, and whether a session still runs in it.
	CloseTerminal  func(handle string) (ok bool, why string)
	SessionPresent func(agent string) (bool, error)
	// Fold is control.fold: fold the day's events into the agent's digest, the one model call, run as `hl fold`. nil serves no agent.fold.
	Fold func(agent string) (ok bool, said string)
	// Marker tells whoever watches the agent's terminal that a control call happened (termchan.Env.Marker); nil tells no one.
	Marker func(agent, kind string, actor map[string]any, text string)
	Now    func() time.Time
	// Typing at a terminal: who holds each terminal's input, the host to type at, and why the usage wall stands in the way ("" for none).
	// Without Holds none of input.take, release, session.input, keys and type is served; without Term the three session ones are not.
	Holds *inputhold.Store
	Term  *term.Term
	Wall  func(agent string) string
	// Structured says whether an agent runs as a structured session, which has no terminal to hold; nil is none.
	Structured func(agent string) bool
	// Roster registers the agent of agent.create, and Emit tells the operations topic its progress; with Controls they serve agent.create.
	Roster *team.Roster
	Emit   func(topic, typ string, data any, agent string)
	// Move is handoff.force_move: the agent's session to another account now, a closed agent's next wake there; nil serves no agent.move.
	Move func(call Call, agent, account string) (ok bool, said string, err error)
}

// Register adds the methods the Env has the ports for.
func Register(s *rpc.Server, e *Env) {
	if e.Controls != nil {
		s.Register("session.start", rpc.Method{Role: "agent", Write: true, Handler: e.start})
		s.Register("session.wake", rpc.Method{Role: "agent", Write: true, Handler: e.wake})
		s.Register("session.restart", rpc.Method{Role: "agent", Write: true, Handler: e.restart})
		s.Register("session.park", rpc.Method{Role: "agent", Write: true, Handler: e.park})
		s.Register("session.stop", rpc.Method{Role: "agent", Write: true, Handler: e.stop})
	}
	if e.Controls != nil && e.Roster != nil {
		s.Register("agent.create", rpc.Method{Role: "founder", Write: true, Handler: e.create})
	}
	if e.Move != nil {
		s.Register("agent.move", rpc.Method{Role: "agent", Write: true, Handler: e.move})
	}
	if e.DB != nil {
		s.Register("agent.unstall", rpc.Method{Role: "founder", Write: true, Handler: e.unstall})
	}
	if e.Fold != nil {
		s.Register("agent.fold", rpc.Method{Role: "agent", Write: true, Handler: e.fold})
	}
	if e.Procs != nil {
		s.Register("session.interrupt", rpc.Method{Role: "member", Write: true, Handler: e.interrupt})
	}
}

func bad(msg, field string) error { return rpc.BadRequestError(msg, field, rpc.InvalidParams) }

func (e *Env) agent(params map[string]any) (string, error) {
	name, _ := params["agent"].(string)
	if name == "" {
		return "", bad("agent is a name", "agent")
	}
	for _, n := range paths.ListAgents(e.Home) {
		if n == name {
			return name, nil
		}
	}
	err := rpc.NotFoundError("no such agent: " + name)
	err.Data = map[string]any{"what": "agent", "name": name}
	return "", err
}

func flag(params map[string]any, key string) (bool, error) {
	v, present := params[key]
	if !present {
		return false, nil
	}
	b, ok := v.(bool)
	if !ok {
		return false, bad(key+" is true or false", key)
	}
	return b, nil
}

// refusal is a refusal whose gate is read off its words.
func refusal(why string) error {
	gate := "state"
	if strings.Contains(why, TypingElsewhere) {
		gate = "terminal_lock"
	}
	return rpc.Refused(why, gate, "")
}

func done(ok bool, said string, err error) (map[string]any, error) {
	if err != nil {
		return nil, err
	}
	if !ok {
		return nil, refusal(said)
	}
	return map[string]any{"accepted": true, "said": said}, nil
}

func callOf(c *rpc.Conn) Call { return Call{Person: c.Caller.Allows("member"), By: c.Caller.Actor()} }

// run runs fn(agent) and records its result as the effect of this request_id. With hold the agent's terminal lock is held across it
// (terminal.close); without, the lifecycle takes whatever lock it needs itself.
func (e *Env) run(c *rpc.Conn, name string, params map[string]any, hold bool, fn func(call Call, agent string) (map[string]any, error)) (any, error) {
	agent, err := e.agent(params)
	if err != nil {
		return nil, err
	}
	var result map[string]any
	if hold {
		h := lock.TakeTerminal(e.Home, agent)
		if h == nil {
			return nil, rpc.Refused(agent+": "+TypingElsewhere, "terminal_lock", "")
		}
		result, err = fn(callOf(c), agent)
		h.Release()
	} else {
		result, err = fn(callOf(c), agent)
	}
	if err != nil {
		return nil, err
	}
	if result, err = c.Effect(func(*sql.Tx) (map[string]any, error) { return result, nil }); err != nil {
		return nil, err
	}
	if e.Marker != nil {
		e.Marker(agent, "control", c.Caller.ActorInfo(), name)
	}
	return result, nil
}

func (e *Env) start(c *rpc.Conn, params map[string]any) (any, error) {
	if params["account"] != nil {
		return nil, bad("account is not served yet: a session starts on the account the daemon picks", "account")
	}
	var repo, base string
	for _, k := range []string{"repo", "base"} {
		v := params[k]
		if v == nil {
			continue
		}
		s, ok := v.(string)
		if !ok {
			return nil, bad(k+" is a string", k)
		}
		if k == "repo" {
			repo = s
		} else {
			base = s
		}
	}
	if base == "" {
		base = "main"
	}
	return e.run(c, "session.start", params, false, func(call Call, agent string) (map[string]any, error) {
		return done(e.Controls.Start(call, agent, repo, base))
	})
}

func (e *Env) wake(c *rpc.Conn, params map[string]any) (any, error) {
	return e.run(c, "session.wake", params, false, func(call Call, agent string) (map[string]any, error) {
		row, err := db.GetAgentState(e.DB, agent)
		if err != nil {
			return nil, err
		}
		if row != nil && row.State == states.Stalled { // a wake of a stalled agent clears the stall, as the verb does
			cleared, said, err := e.Unstall(agent)
			return done(err == nil && cleared, said, err)
		}
		ok, why, err := e.Controls.Wake(call, agent)
		if err != nil {
			return nil, err
		}
		if ok {
			return done(true, agent+": "+why, nil)
		}
		return done(false, agent+": not woken - "+why, nil)
	})
}

// Unstall clears a stall, the one state the daemon will not leave alone (control.unstall). cleared is false when the agent was not STALLED.
func (e *Env) Unstall(agent string) (cleared bool, said string, err error) {
	row, err := db.GetAgentState(e.DB, agent)
	if err != nil {
		return false, "", err
	}
	if row == nil || row.State != states.Stalled {
		state := "no state"
		if row != nil {
			state = row.State
		}
		return false, fmt.Sprintf("%s: not STALLED (%s)", agent, state), nil
	}
	if err := db.SetAgentState(e.DB, agent, states.Queued, 0, "cleared by a human"); err != nil {
		return false, "", err
	}
	for _, col := range []string{"failed_attempts", "throttle_count", "wake_failures"} {
		if err := db.ResetCounter(e.DB, agent, col); err != nil {
			return false, "", err
		}
	}
	for _, key := range []string{"dialog_since:", "dialog_title:"} {
		if err := db.ClearCursor(e.DB, key+agent); err != nil {
			return false, "", err
		}
	}
	return true, agent + ": STALLED -> QUEUED", nil
}

func (e *Env) restart(c *rpc.Conn, params map[string]any) (any, error) {
	fresh, err := flag(params, "fresh")
	if err != nil {
		return nil, err
	}
	force, err := flag(params, "force")
	if err != nil {
		return nil, err
	}
	return e.run(c, "session.restart", params, false, func(call Call, agent string) (map[string]any, error) {
		return done(e.Controls.Restart(call, agent, fresh, force))
	})
}

// move is agent.move. A forced move ends a session, which is the founder's call (a person's, from a terminal or the screen), not an agent's.
func (e *Env) move(c *rpc.Conn, params map[string]any) (any, error) {
	if !c.Caller.Allows("member") {
		return nil, rpc.Refused("a forced move ends a session: it is the founder's, from a terminal or the screen, not an agent's", "role", "")
	}
	account, _ := params["account"].(string)
	if account == "" {
		return nil, bad("account is the account to move the agent to", "account")
	}
	return e.run(c, "agent.move", params, false, func(call Call, agent string) (map[string]any, error) {
		return done(e.Move(call, agent, account))
	})
}

// unstall is agent.unstall: a stalled agent goes to queued (Unstall); one that is not is refused with the action's own words.
func (e *Env) unstall(c *rpc.Conn, params map[string]any) (any, error) {
	return e.run(c, "agent.unstall", params, true, func(_ Call, agent string) (map[string]any, error) {
		cleared, said, err := e.Unstall(agent)
		return done(err == nil && cleared, said, err)
	})
}

func (e *Env) fold(c *rpc.Conn, params map[string]any) (any, error) {
	return e.run(c, "agent.fold", params, false, func(_ Call, agent string) (map[string]any, error) {
		ok, said := e.Fold(agent)
		return done(ok, said, nil)
	})
}

func (e *Env) park(c *rpc.Conn, params map[string]any) (any, error) {
	return e.run(c, "session.park", params, false, func(call Call, agent string) (map[string]any, error) {
		return done(e.Controls.Park(call, agent))
	})
}

// stop: close and exit both end the session by closing the CLI's stdin (the CLI exits when its input ends, so nothing is typed);
// kill ends the process group at once, and is for a member or above.
func (e *Env) stop(c *rpc.Conn, params map[string]any) (any, error) {
	mode, _ := params["mode"].(string)
	fresh, err := flag(params, "fresh")
	if err != nil {
		return nil, err
	}
	if mode != "exit" && mode != "close" && mode != "kill" {
		return nil, bad("mode is exit, close or kill", "mode")
	}
	if fresh && mode != "close" {
		return nil, bad("fresh is for close only", "fresh")
	}
	if mode == "kill" {
		if !c.Caller.Allows("member") {
			return nil, rpc.Refused(fmt.Sprintf("session.stop kill is for a member or above; this caller is %s", c.Caller.Role), "role", "")
		}
		return e.run(c, "session.stop", params, false, func(_ Call, agent string) (map[string]any, error) {
			if e.Procs == nil || !e.Procs.Structured(agent) {
				return nil, rpc.Refused(agent+" has no structured session to kill", "state", "")
			}
			if err := e.Procs.Kill(agent); err != nil {
				return nil, rpc.Refused(agent+": could not end its session: "+err.Error(), "state", "")
			}
			return done(true, agent+": session ended", nil)
		})
	}
	return e.run(c, "session.stop", params, false, func(call Call, agent string) (map[string]any, error) {
		return done(e.Controls.Close(call, agent, fresh))
	})
}

// interrupt stops the running turn of a structured session by control request. A terminal agent has no turn to reach from here.
func (e *Env) interrupt(c *rpc.Conn, params map[string]any) (any, error) {
	return e.run(c, "session.interrupt", params, false, func(_ Call, agent string) (map[string]any, error) {
		if !e.Procs.Structured(agent) {
			return nil, rpc.Refused(agent+" has no structured session to interrupt", "state", "")
		}
		if err := e.Procs.Interrupt(agent); err != nil {
			return nil, rpc.Refused(agent+": not interrupted: "+err.Error(), "state", "")
		}
		return done(true, agent+": interrupted", nil)
	})
}

func (e *Env) terminalClose(c *rpc.Conn, params map[string]any) (any, error) {
	return e.run(c, "terminal.close", params, true, func(_ Call, agent string) (map[string]any, error) {
		if err := e.holderGate(c, agent); err != nil {
			return nil, err
		}
		row, err := db.GetAgentState(e.DB, agent)
		if err != nil {
			return nil, err
		}
		if row == nil || row.Terminal.String == "" {
			return nil, rpc.Refused(agent+" has no terminal", "state", "")
		}
		handle := row.Terminal.String
		present, err := e.SessionPresent(agent)
		if err != nil {
			return nil, err
		}
		if row.ClosedAt.Int64 == 0 || present {
			return nil, rpc.Refused(agent+" has a session running; stop it first", "state", "")
		}
		if ok, why := e.CloseTerminal(handle); !ok {
			return nil, rpc.Refused(fmt.Sprintf("%s: its terminal would not close: %s", agent, why), "state", "")
		}
		if err := db.SetTerminal(e.DB, agent, "", row.Worktree.String); err != nil {
			return nil, err
		}
		if _, err := db.AddEvent(e.DB, agent, "terminal_closed", db.KV{K: "handle", V: handle}); err != nil {
			return nil, err
		}
		if err := e.Holds.Release(agent); err != nil { // a hold on a terminal that is gone is over
			return nil, err
		}
		return map[string]any{"accepted": true, "said": agent + ": terminal closed"}, nil
	})
}
