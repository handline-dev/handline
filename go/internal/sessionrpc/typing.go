package sessionrpc

import (
	"strings"
	"unicode"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/inputhold"
	"github.com/handline/handline/go/internal/lock"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/term"
)

// input.take and release, and session.input, keys and type: what a person at a terminal does (handline/daemon/rpc_input.py, rpc_typing.py).
// Every typing call takes the agent's terminal lock for as long as it types, so the tick types nothing beside it, and passes the gates in
// order: terminal lock, input holder, and for input the usage wall and an open dialog. keys and type pass neither of the last two, as a
// person pressing a key has looked at the screen. The one who types counts as typing for the idle hold.

const (
	maxText = 8000
	maxKeys = 64
)

// keyBytes is what a person's named key is, as the bytes a terminal sends for it; "enter" is the host's own Enter.
var keyBytes = map[string]string{"esc": term.Escape, "enter": "", "up": "\x1b[A", "down": "\x1b[B", "tab": "\t", "shift-tab": "\x1b[Z", "ctrl-c": "\x03",
	"0": "0", "1": "1", "2": "2", "3": "3", "4": "4", "5": "5", "6": "6", "7": "7", "8": "8", "9": "9"}

const keyNames = "esc, enter, up, down, tab, shift-tab, ctrl-c, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9"

// RegisterTyping adds input.take and release, and session.input, keys and type when the Env has the terminal to type at. They stand on
// their own: the daemon that serves them needs no structured sessions.
func RegisterTyping(s *rpc.Server, e *Env) {
	if e.Holds == nil {
		return
	}
	s.Register("input.take", rpc.Method{Role: "member", Write: true, Handler: e.take})
	s.Register("input.release", rpc.Method{Role: "member", Write: true, Handler: e.release})
	if e.Term != nil {
		s.Register("session.input", rpc.Method{Role: "member", Write: true, Handler: e.sessionInput})
		s.Register("session.type", rpc.Method{Role: "member", Write: true, Handler: e.sessionType})
		s.Register("session.keys", rpc.Method{Role: "member", Write: true, Handler: e.sessionKeys})
	}
}

func holderKind(c *rpc.Conn) string {
	if k, _ := c.Caller.Info["kind"].(string); k == "device" {
		return "device"
	}
	return "person"
}

func callerName(c *rpc.Conn) string {
	n, _ := c.Caller.Info["name"].(string)
	return n
}

// holderGate refuses a call while a person or device other than the caller holds the input. The daemon holding it is a short hold, not a
// refusal.
func (e *Env) holderGate(c *rpc.Conn, agent string) error {
	row, err := e.Holds.Holder(agent)
	if err != nil {
		return err
	}
	if row != nil && row.Name != callerName(c) {
		return rpc.Refused(inputhold.HeldWhy(agent, row.Name), "input_holder", "")
	}
	return nil
}

// terminalOnly refuses an agent run as a structured session: it has no terminal to type at, so a client sends chat.send.
func (e *Env) terminalOnly(agent string) error {
	if e.Structured != nil && e.Structured(agent) {
		return rpc.Refused(agent+" runs as a structured session and has no terminal: send it a chat message", "unsupported", "")
	}
	return nil
}

func (e *Env) take(c *rpc.Conn, params map[string]any) (any, error) {
	agent, err := e.agent(params)
	if err != nil {
		return nil, err
	}
	if err := e.terminalOnly(agent); err != nil {
		return nil, err
	}
	if err := e.holderGate(c, agent); err != nil {
		return nil, err
	}
	won, err := e.Holds.Take(agent, holderKind(c), callerName(c))
	if err != nil {
		return nil, err
	}
	if !won { // another took it first
		if err := e.holderGate(c, agent); err != nil {
			return nil, err
		}
		return nil, rpc.Refused(agent+"'s terminal was taken by someone else", "input_holder", "")
	}
	view, err := e.Holds.View(agent)
	if err != nil {
		return nil, err
	}
	return map[string]any{"accepted": true, "said": agent + ": you hold its input", "holder": view[0]}, nil
}

func (e *Env) release(c *rpc.Conn, params map[string]any) (any, error) {
	agent, err := e.agent(params)
	if err != nil {
		return nil, err
	}
	if err := e.terminalOnly(agent); err != nil {
		return nil, err
	}
	row, err := e.Holds.Holder(agent)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return map[string]any{"accepted": true, "said": agent + ": the daemon already holds its input"}, nil
	}
	if err := e.holderGate(c, agent); err != nil { // only its holder gives it back
		return nil, err
	}
	if err := e.Holds.Release(agent); err != nil {
		return nil, err
	}
	return map[string]any{"accepted": true, "said": agent + ": its input is the daemon's again"}, nil
}

// text is the text of a call, refused when it holds a control character outside allowed: Esc and the rest are keys.
func text(params map[string]any, allowed string) (string, error) {
	t, ok := params["text"].(string)
	if !ok || t == "" || len([]rune(t)) > maxText {
		return "", bad("text is 1 to 8000 characters", "text")
	}
	for _, r := range t {
		if !unicode.IsPrint(r) && !strings.ContainsRune(allowed, r) {
			return "", bad("text holds a control character: those are session.keys", "text")
		}
	}
	return t, nil
}

// act takes the lock, passes the gates, sends with send(handle), and leaves the marker of a control call.
func (e *Env) act(c *rpc.Conn, name string, params map[string]any, wall bool, send func(handle string) (bool, string)) (any, error) {
	agent, err := e.agent(params)
	if err != nil {
		return nil, err
	}
	if err := e.terminalOnly(agent); err != nil {
		return nil, err
	}
	hold := lock.TakeTerminal(e.Home, agent)
	if hold == nil {
		return nil, rpc.Refused(agent+": "+TypingElsewhere, "terminal_lock", "")
	}
	defer hold.Release()
	if err := e.holderGate(c, agent); err != nil {
		return nil, err
	}
	handle, err := db.Terminal(e.DB, agent)
	if err != nil {
		return nil, err
	}
	if handle == "" || !e.Term.Backend.Available() {
		return nil, rpc.Refused(agent+" has no terminal", "state", "")
	}
	if wall {
		if e.Wall != nil {
			if why := e.Wall(agent); why != "" {
				return nil, rpc.Refused(why, "wall", "")
			}
		}
		if b := e.Term.ScreenBlocker(handle); b != nil {
			return nil, rpc.Refused(b.Reason()+"; use session.keys or prompt.answer", "screen_blocker", "")
		}
	}
	if ok, why := send(handle); !ok {
		return nil, rpc.Refused(agent+": nothing sent - "+why, "state", "")
	}
	if err := db.SetCursor(e.DB, "typed_at:"+agent, e.now().Unix()); err != nil {
		return nil, err
	}
	if e.Marker != nil {
		e.Marker(agent, "control", c.Caller.ActorInfo(), "")
	}
	if err := e.Holds.Touch(agent, callerName(c)); err != nil {
		return nil, err
	}
	return map[string]any{"accepted": true, "said": agent + ": " + name + " sent"}, nil
}

func (e *Env) sessionInput(c *rpc.Conn, params map[string]any) (any, error) {
	t, err := text(params, "\n\t")
	if err != nil {
		return nil, err
	}
	enter := true
	if v, present := params["enter"]; present && v != nil {
		if enter, _ = v.(bool); !isBool(v) {
			return nil, bad("enter is true or false", "enter")
		}
	}
	return e.act(c, "session.input", params, true, func(handle string) (bool, string) {
		// a prompt line is for a session, never for a shell
		if s := e.Term.Backend.ReadScreen(handle, 40); s != nil && s.Source == "screen" && s.Tail != nil && term.ShellPromptIn(s.Tail) {
			return false, "a shell prompt is showing, not a session; nothing typed"
		}
		return e.Term.Send(handle, t, enter)
	})
}

func isBool(v any) bool { _, ok := v.(bool); return ok }

func (e *Env) sessionType(c *rpc.Conn, params map[string]any) (any, error) {
	t, err := text(params, "\t")
	if err != nil {
		return nil, err
	}
	return e.act(c, "session.type", params, false, func(handle string) (bool, string) { return e.Term.Send(handle, t, false) })
}

func (e *Env) sessionKeys(c *rpc.Conn, params map[string]any) (any, error) {
	list, _ := params["keys"].([]any)
	keys := make([]string, 0, len(list))
	for _, k := range list {
		name, _ := k.(string)
		if _, known := keyBytes[name]; !known {
			return nil, bad("keys is 1 to 64 of "+keyNames, "keys")
		}
		keys = append(keys, name)
	}
	if len(keys) < 1 || len(keys) > maxKeys {
		return nil, bad("keys is 1 to 64 of "+keyNames, "keys")
	}
	return e.act(c, "session.keys", params, false, func(handle string) (bool, string) {
		for _, k := range keys {
			var ok bool
			var out string
			if k == "enter" {
				ok, out = e.Term.Send(handle, "", true)
			} else {
				ok, out = e.Term.Send(handle, keyBytes[k], false)
			}
			if !ok {
				return false, k + ": " + truncate(out, 200)
			}
		}
		return true, ""
	})
}

func truncate(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[:n])
	}
	return s
}
