// Package legacyverbs answers the legacy `{verb}` requests that Python's `hl` still sends over the control socket (handline/daemon/
// socket_server.py): one {ok, said} reply, then a hangup. The checks and the words are the Python daemon's; the work is the Go writes that
// already exist (the supervisor's say and pushes, the session controls, the agent roster, the prompt answers).
//
// Served: say, push, push_answer, notify_cancelled, close, restart, agent_set, answer_block. Not served, because nothing sends them any
// more (the UI and the bridge call the JSON-RPC methods): message, wake, park, fold, move, start and trust. land, land_pass and
// land_authorize are the lands package's. A verb not served here is left to the server's own answer.
//
// None of these verbs acts on the strength of who the caller is (Python's TOKEN_VERBS are the land verbs only): the socket is private to the
// user, and the requester and speaker in a request are taken at their word, as OCP_AGENT always was.
package legacyverbs

import (
	"database/sql"
	"fmt"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/prompts"
	"github.com/handline/handline/go/internal/pyval"
	"github.com/handline/handline/go/internal/sessionrpc"
	"github.com/handline/handline/go/internal/supervisor"
)

// Env is what the verbs run on. A nil port leaves the verbs that need it unserved.
type Env struct {
	DB   *sql.DB
	Home string
	// Sup is the telling layer: say, push, push_answer and notify_cancelled.
	Sup *supervisor.Env
	// Controls is the session lifecycle, for close and restart; it may answer nil while the daemon has no sessions to drive.
	Controls func() sessionrpc.Controls
	// SetByAgent is an agent's own change of its settings (hrwrites.Env.SetByAgent).
	SetByAgent func(agent string, values map[string]string, speaker string) (bool, string)
	// Prompts answers a prompt block.
	Prompts *prompts.Env
	Log     func(format string, args ...any)
}

type verb func(e *Env, p map[string]any) (ok bool, said string, err error)

var verbs = map[string]verb{
	"say": (*Env).say, "push": (*Env).push, "push_answer": (*Env).pushAnswer, "notify_cancelled": (*Env).notifyCancelled,
	"close": (*Env).close, "restart": (*Env).restart, "agent_set": (*Env).agentSet, "answer_block": (*Env).answerBlock,
}

// Legacy answers a legacy request of a local connection: the reply, or nil for a verb this package does not serve.
func (e *Env) Legacy(p map[string]any, _ int) (reply map[string]any) {
	name, _ := p["verb"].(string)
	fn := verbs[name]
	if fn == nil || !e.serves(name) {
		return nil
	}
	defer func() { // a bad request must reply, not kill the connection
		if r := recover(); r != nil {
			reply = map[string]any{"ok": false, "said": fmt.Sprintf("%s: %v", name, r)}
		}
	}()
	ok, said, err := fn(e, p)
	if err != nil {
		return map[string]any{"ok": false, "said": name + ": " + err.Error()}
	}
	return map[string]any{"ok": ok, "said": said}
}

// serves says whether the ports a verb runs on are there.
func (e *Env) serves(name string) bool {
	switch name {
	case "say", "push", "push_answer", "notify_cancelled":
		return e.Sup != nil
	case "agent_set":
		return e.SetByAgent != nil
	case "answer_block":
		return e.Prompts != nil
	}
	return true // close and restart answer that a daemon with no sessions does not drive them
}

// missing is Python's KeyError for a key the request has to carry: its text is the key quoted.
type missing string

func (m missing) Error() string { return "'" + string(m) + "'" }

func need(p map[string]any, keys ...string) error {
	for _, k := range keys {
		if _, has := p[k]; !has {
			return missing(k)
		}
	}
	return nil
}

func text(p map[string]any, key string) (string, error) {
	if err := need(p, key); err != nil {
		return "", err
	}
	return pyval.Str(p[key]), nil
}

func number(p map[string]any, key string) (int64, error) {
	if err := need(p, key); err != nil {
		return 0, err
	}
	n, ok := pyval.Int(p[key])
	if !ok {
		return 0, fmt.Errorf("%s is a number, not %s", key, pyval.Str(p[key]))
	}
	return n, nil
}

// optionalNumber is key as a number; 0 when it is absent or null (Python's None).
func optionalNumber(p map[string]any, key string) (int64, error) {
	if p[key] == nil {
		return 0, nil
	}
	return number(p, key)
}

// within runs fn in a transaction of its own.
func (e *Env) within(fn func(q db.Q) error) error {
	tx, err := e.DB.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err := fn(tx); err != nil {
		return err
	}
	return tx.Commit()
}

func (e *Env) agentNamed(name string) bool {
	for _, a := range paths.ListAgents(e.Home) {
		if a == name {
			return true
		}
	}
	return false
}
