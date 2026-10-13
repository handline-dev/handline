package lands

import (
	"database/sql"
	"fmt"
	"strconv"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/pyval"
	"github.com/handline/handline/go/internal/rpc"
)

// The land verbs and who may send them (handline/daemon/socket_server.py).
//
//	land_authorize  no peer check: the one caller that holds the session's key, which only the session's own PreToolUse hook can read
//	                (it runs outside the Bash sandbox). It names the agent, the verb (land or land_pass) and the sha as typed.
//	land, land_pass the process of the session `hl launch` recorded for the agent (Tokens.Verify, by the socket's peer pid), and only
//	                once for a vouch of exactly this agent, verb and sha made within GrantTTL (Grants.Take); then the verb's own rule:
//	                land is the merger's alone, land_pass an auditor's.

// tokenVerbs are the verbs that act on the strength of who the caller is.
var tokenVerbs = map[string]bool{"land": true, "land_pass": true}

func shown(v any) string {
	s := pyval.Str(v)
	if r := []rune(s); len(r) > 60 {
		s = string(r[:60])
	}
	return strconv.Quote(s)
}

// Legacy answers a legacy `{verb}` request of a local connection whose peer process is pid: the reply, or nil for a verb this daemon
// does not serve.
func (s *Service) Legacy(payload map[string]any, pid int) map[string]any {
	verb, _ := payload["verb"].(string)
	switch verb {
	case "land_authorize":
		return s.authorize(payload)
	case "land", "land_pass":
	default:
		return nil
	}
	agent, isText := payload["agent"].(string)
	if !isText || agent == "" || !s.Tokens.Verify(agent, pid) {
		s.log("  %s refused: caller is not the session of %s", verb, shown(payload["agent"]))
		who := "the caller"
		if agent != "" {
			who = agent
		}
		return map[string]any{"ok": false, "said": who + " is not this request's session: it must come from a session `hl launch` " +
			"recorded for that agent (relaunch an older one)"}
	}
	if !s.Grants.Take(agent, verb, pyval.Str(payload["sha"])) {
		s.log("  %s refused: nothing the session's own command vouched for (%s)", verb, shown(agent))
		return map[string]any{"ok": false, "said": verb + " is accepted only from the session's own `hl land` command, " +
			"not from other code it runs (a test run, a script): run it as a Bash call of its own, with no pipe, redirect, `&&` or `$`"}
	}
	ok, said := s.run(verb, payload, agent, s.inTx)
	return map[string]any{"ok": ok, "said": said}
}

// authorize is the session's PreToolUse hook vouching for the `hl land` command it is about to run (session/landgate.py).
func (s *Service) authorize(payload map[string]any) map[string]any {
	agent, _ := payload["agent"].(string)
	verb, _ := payload["land_verb"].(string)
	if !tokenVerbs[verb] || agent == "" || !pyval.Truthy(payload["key"]) || !s.Tokens.KeyOK(agent, pyval.Str(payload["key"])) {
		s.log("  land_authorize refused for %s", shown(payload["agent"]))
		return map[string]any{"ok": false, "said": "not authorized"}
	}
	s.Grants.Grant(agent, verb, pyval.Str(payload["sha"]))
	s.log("  land_authorize: %s vouched for %s %s", shown(agent), verb, truncate(strconv.Quote(pyval.Str(payload["sha"])), 42))
	return map[string]any{"ok": true, "said": "vouched"}
}

// within runs fn in a transaction: one of its own, or the one a write call already holds.
type within func(fn func(db.Q) error) error

// step is the part of a verb that reads and writes the database.
type step func(q db.Q) (ok bool, said string, err error)

// prepare is a vouched verb of agent's own session up to its transaction: every check that runs git or python, so that none runs with
// the write lock held. The step is nil when the verb is refused, and said then says why.
func (s *Service) prepare(verb string, p map[string]any, agent string) (do step, said string) {
	need := func(keys ...string) (string, bool) {
		for _, k := range keys {
			if _, has := p[k]; !has {
				return fmt.Sprintf("%s: '%s'", verb, k), false
			}
		}
		return "", true
	}
	if verb == "land_pass" {
		if said, ok := need("sha"); !ok {
			return nil, said
		}
		full, why := s.PreparePass(pyval.Str(p["sha"]), agent)
		if full == "" {
			return nil, why
		}
		return func(q db.Q) (bool, string, error) { return s.recordPass(q, full, agent) }, ""
	}
	if said, ok := need("sha", "message"); !ok {
		return nil, said
	}
	message, isText := p["message"].(string)
	tree, treeText := p["tree"].(string)
	if !isText || (p["tree"] != nil && !treeText) {
		return nil, verb + ": message and tree are text"
	}
	prepared, why := s.Prepare(RequestIn{Sha: pyval.Str(p["sha"]), Message: message, Tree: tree, Caller: agent, Fix: p["fix"], Squash: pyval.Truthy(p["squash"])})
	if prepared == nil {
		return nil, why
	}
	return func(q db.Q) (bool, string, error) {
		queued, why, err := s.Check(q, prepared)
		if err != nil || queued == nil {
			return false, why, err
		}
		said, err := s.Queue(q, queued)
		return err == nil, said, err
	}, ""
}

// run is a vouched verb of agent's own session.
func (s *Service) run(verb string, p map[string]any, agent string, tx within) (bool, string) {
	do, said := s.prepare(verb, p, agent)
	if do == nil {
		return false, said
	}
	return s.finish(verb, do, tx)
}

func (s *Service) finish(verb string, do step, tx within) (ok bool, said string) {
	if err := tx(func(q db.Q) (err error) {
		ok, said, err = do(q)
		return err
	}); err != nil {
		return false, verb + ": " + err.Error()
	}
	return ok, said
}

// inTx is a transaction of its own over the database.
func (s *Service) inTx(fn func(db.Q) error) error {
	tx, err := s.DB.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err := fn(tx); err != nil {
		return err
	}
	return tx.Commit()
}

// Register adds land, land.pass and land.authorize, the same verbs as JSON-RPC calls of the agent's own session. A caller is the
// session that sent it (the socket's peer pid, which is what makes the connection an agent's), so land.authorize also needs the agent
// it names to be that one (gate not_self) and the session's key; land and land.pass need the vouch of land.authorize, as above.
func Register(srv *rpc.Server, s *Service) {
	srv.Register("land.authorize", rpc.Method{Role: "agent", Write: true, Handler: s.authorizeCall})
	srv.Register("land", rpc.Method{Role: "agent", Write: true, Handler: s.landCall("land")})
	srv.Register("land.pass", rpc.Method{Role: "agent", Write: true, Handler: s.landCall("land_pass")})
}

func badParam(message, field string) *rpc.Error {
	return rpc.BadRequestError(message, field, rpc.InvalidParams)
}

func text(p map[string]any, key string) (string, error) {
	v, ok := p[key].(string)
	if !ok || strings.TrimSpace(v) == "" {
		return "", badParam(key+" is text and not empty", key)
	}
	return v, nil
}

// ownSession refuses every caller but an agent's own session: these are not calls of the founder or of a device.
func ownSession(c *rpc.Conn, method string) error {
	if c.Caller.Role != "agent" {
		return rpc.Refused(method+" is for an agent's own session; this caller is "+c.Caller.Role, "role", "")
	}
	return nil
}

func (s *Service) authorizeCall(c *rpc.Conn, p map[string]any) (any, error) {
	if err := ownSession(c, "land.authorize"); err != nil {
		return nil, err
	}
	agent, err := text(p, "agent")
	if err != nil {
		return nil, err
	}
	verb, _ := p["land_verb"].(string)
	if !tokenVerbs[verb] {
		return nil, badParam("land_verb is land or land_pass", "land_verb")
	}
	sha, err := text(p, "sha")
	if err != nil {
		return nil, err
	}
	key, err := text(p, "key")
	if err != nil {
		return nil, err
	}
	if err := c.RequireSelf(agent); err != nil {
		return nil, err
	}
	return c.Effect(func(*sql.Tx) (map[string]any, error) {
		if r := s.authorize(map[string]any{"agent": agent, "land_verb": verb, "sha": sha, "key": key}); r["ok"] != true {
			return nil, rpc.Refused(r["said"].(string), "session_token", "")
		}
		return map[string]any{"accepted": true, "said": "vouched"}, nil
	})
}

func (s *Service) landCall(verb string) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		if err := ownSession(c, strings.ReplaceAll(verb, "_", ".")); err != nil {
			return nil, err
		}
		sha, err := text(p, "sha")
		if err != nil {
			return nil, err
		}
		agent, _ := c.Caller.Info["name"].(string)
		if !s.Tokens.Verify(agent, c.Pid) {
			return nil, rpc.Refused(agent+" is not this request's session: it must come from a session `hl launch` recorded for that agent", "session_token", "")
		}
		payload := map[string]any{"sha": sha}
		if verb == "land" {
			if payload["message"], err = text(p, "message"); err != nil {
				return nil, err
			}
			for _, k := range []string{"tree", "fix", "squash"} {
				if v, has := p[k]; has {
					payload[k] = v
				}
			}
		}
		if !s.Grants.Take(agent, verb, sha) {
			return nil, rpc.Refused(verb+" is accepted only from the session's own `hl land` command, not from other code it runs (a test run, a script): "+
				"run it as a Bash call of its own, with no pipe, redirect, `&&` or `$`",
				"session_token", "")
		}
		do, said := s.prepare(verb, payload, agent) // before the write transaction: git and python run in it
		if do == nil {
			return nil, rpc.Refused(said, "state", "")
		}
		return c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			ok, said := s.finish(verb, do, func(fn func(db.Q) error) error { return fn(tx) })
			if !ok {
				return nil, rpc.Refused(said, "state", "")
			}
			return map[string]any{"accepted": true, "said": said}, nil
		})
	}
}
