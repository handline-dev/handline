package sessionrpc

import (
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/operations"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/textsafe"
	"github.com/handline/handline/go/internal/yamlcompat"
)

// agent.create is `hl agent register` followed by `hl spawn` as one long-running operation. It answers at once with the operation's id; a
// goroutine registers the agent (team.Roster.Register) and starts it through Controls.Start, the path session.start takes, and each step
// is a row update and an operation.progress event on the operations topic. The operation row and the request_id's reply are written in
// one transaction before the goroutine starts, so a retry after a lost reply starts nothing again. A failure in the start leaves the agent
// registered, as the two verbs would, and the operation says so; nothing is cleaned up.

const (
	createKind   = "agent.create"
	createKeep   = 7 * 24 * time.Hour
	createSteps  = 2
	stepRegister = "register"
	stepStart    = "start"
)

func (e *Env) create(c *rpc.Conn, params map[string]any) (any, error) {
	name, err := createText(params, "name", true)
	if err != nil {
		return nil, err
	}
	role, err := createText(params, "role", true)
	if err != nil {
		return nil, err
	}
	effort, err := createText(params, "effort", false)
	if err != nil {
		return nil, err
	}
	if effort != "" && !contains(team.ValidEffort, effort) {
		return nil, bad("effort is one of "+strings.Join(team.ValidEffort, ", "), "effort")
	}
	if params["account"] != nil {
		return nil, bad("a new agent's account is chosen when it starts; none can be named yet", "account")
	}
	if strings.Contains(name, "/") || strings.HasPrefix(name, ".") || textsafe.HasUnsafe(name) || !yamlcompat.IsPlainText(name) {
		return nil, bad("name is an agent's name, plain text", "name")
	}
	repo, err := createText(params, "repo", true)
	if err != nil {
		return nil, err
	}
	base, err := createText(params, "base", false)
	if err != nil {
		return nil, err
	}
	model, err := createText(params, "model", false)
	if err != nil {
		return nil, err
	}
	parent, err := createText(params, "parent", false)
	if err != nil {
		return nil, err
	}
	if st, serr := os.Stat(repo); serr != nil || !st.IsDir() {
		return nil, notFound("repo", repo, "no repo "+repo)
	}
	known := map[string]bool{}
	for _, a := range paths.ListAgents(e.Home) {
		known[a] = true
	}
	if parent != "" {
		if !known[parent] {
			return nil, notFound("agent", parent, "no agent "+parent)
		}
		if team.ParentName(name) != parent {
			return nil, bad("name is "+parent+".<something> under that parent", "name")
		}
	}
	if known[name] {
		return nil, rpc.Refused(name+" is already an agent", "state", "")
	}
	if abs, aerr := filepath.Abs(repo); aerr == nil {
		repo = abs
	}
	if base == "" {
		base = "main"
	}

	op := newOperationID()
	operations.Begin(op) // before the row exists, so nothing reads a running row of a live operation as dead
	result, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		rows, qerr := tx.Query("SELECT id FROM operations WHERE kind = ? AND agent = ? AND state = 'running'", createKind, name)
		if qerr != nil {
			return nil, qerr
		}
		var others []string
		for rows.Next() {
			var id string
			if qerr = rows.Scan(&id); qerr != nil {
				rows.Close()
				return nil, qerr
			}
			others = append(others, id)
		}
		rows.Close()
		for _, id := range others {
			if operations.Live(id) {
				return nil, rpc.Refused(name+" is already being created", "state", "")
			}
		}
		now := e.now().Unix()
		if _, qerr = tx.Exec("DELETE FROM operations WHERE updated_at < ?", now-int64(createKeep/time.Second)); qerr != nil {
			return nil, qerr
		}
		if _, qerr = tx.Exec("INSERT INTO operations(id, kind, state, step, steps_done, steps_total, message, agent, created_at, updated_at) "+
			"VALUES(?, ?, 'running', NULL, 0, ?, ?, ?, ?, ?)", op, createKind, createSteps, "creating "+name, name, now, now); qerr != nil {
			return nil, qerr
		}
		return map[string]any{"accepted": true, "said": "creating " + name, "operation": op}, nil
	})
	if err != nil {
		operations.End(op)
		return nil, err
	}
	e.announce(op)
	opts := team.RegisterOpts{Role: role, Repo: repo}
	if model != "" {
		opts.Model = &model
	}
	if effort != "" {
		opts.Effort = &effort
	}
	go e.runCreate(op, name, repo, base, opts, callOf(c))
	return result, nil
}

func (e *Env) runCreate(op, name, repo, base string, opts team.RegisterOpts, call Call) {
	step := stepRegister
	defer operations.End(op)
	defer func() {
		if r := recover(); r != nil { // whatever stopped it is the operation's message, never a dead goroutine
			e.progress(op, map[string]any{"state": "failed", "message": fmt.Sprintf("%s failed: %v", step, r),
				"result": map[string]any{"agent": name, "registered": step == stepStart}})
		}
	}()
	e.progress(op, map[string]any{"step": step, "message": "registering " + name})
	if _, err := e.Roster.Register(name, opts); err != nil {
		e.progress(op, map[string]any{"state": "failed", "message": fmt.Sprintf("%s failed: %v", step, err),
			"result": map[string]any{"agent": name, "registered": false}})
		return
	}
	step = stepStart
	e.progress(op, map[string]any{"step": step, "steps_done": 1, "message": "starting " + name})
	ok, said, err := e.Controls.Start(call, name, repo, base)
	said = strings.TrimSpace(said)
	switch {
	case err != nil:
		e.progress(op, map[string]any{"state": "failed", "message": fmt.Sprintf("%s failed: %v", step, err),
			"result": map[string]any{"agent": name, "registered": true}})
	case !ok:
		e.progress(op, map[string]any{"state": "failed", "message": fmt.Sprintf("%s is registered; starting it failed: %s", name, said),
			"result": map[string]any{"agent": name, "registered": true}})
	default:
		e.progress(op, map[string]any{"state": "done", "step": nil, "steps_done": createSteps, "message": said,
			"result": map[string]any{"agent": name, "registered": true}})
	}
}

// progress changes an operation's row and tells the operations topic, in that order.
func (e *Env) progress(op string, fields map[string]any) {
	sets, args := []string{"updated_at = ?"}, []any{e.now().Unix()}
	for _, k := range []string{"state", "step", "steps_done", "message"} {
		if v, has := fields[k]; has {
			sets, args = append(sets, k+" = ?"), append(args, v)
		}
	}
	if r, has := fields["result"]; has {
		b, _ := json.Marshal(r)
		sets, args = append(sets, "result_json = ?"), append(args, string(b))
	}
	if _, err := e.DB.Exec("UPDATE operations SET "+strings.Join(sets, ", ")+" WHERE id = ?", append(args, op)...); err != nil {
		return
	}
	e.announce(op)
}

// announce sends the operation as the row holds it.
func (e *Env) announce(op string) {
	if e.Emit == nil {
		return
	}
	var kind, state, agent string
	var step, message, resultJSON sql.NullString
	var done int64
	var total sql.NullInt64
	if err := e.DB.QueryRow("SELECT kind, state, step, steps_done, steps_total, message, result_json, agent FROM operations WHERE id = ?", op).
		Scan(&kind, &state, &step, &done, &total, &message, &resultJSON, &agent); err != nil {
		return
	}
	data := map[string]any{"id": op, "kind": kind, "state": state, "step": nullable(step), "steps_done": done, "steps_total": nil,
		"message": nullable(message), "result": nil, "agent": agent}
	if total.Valid {
		data["steps_total"] = total.Int64
	}
	if resultJSON.Valid && resultJSON.String != "" {
		var r any
		if json.Unmarshal([]byte(resultJSON.String), &r) == nil {
			data["result"] = r
		}
	}
	e.Emit("operations", "operation.progress", data, agent)
}

func nullable(s sql.NullString) any {
	if s.Valid {
		return s.String
	}
	return nil
}

func (e *Env) now() time.Time {
	if e.Now != nil {
		return e.Now()
	}
	return time.Now()
}

func newOperationID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

func notFound(what, name, msg string) error {
	err := rpc.NotFoundError(msg)
	err.Data = map[string]any{"what": what, "name": name}
	return err
}

// createText is a param that is text and not blank; absent is "" when it is not required.
func createText(p map[string]any, key string, required bool) (string, error) {
	v := p[key]
	if v == nil && !required {
		return "", nil
	}
	s, ok := v.(string)
	if !ok || strings.TrimSpace(s) == "" {
		return "", bad(key+" is text and not empty", key)
	}
	return s, nil
}
