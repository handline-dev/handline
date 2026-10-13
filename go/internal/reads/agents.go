package reads

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"github.com/handline/handline/go/internal/term"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/team"
)

// roleLine is the first line of the agent's ROLE.md that is not blank and not a heading, as one line.
func (e *Env) roleLine(name string) string {
	b, err := os.ReadFile(filepath.Join(e.Home, "agents", name, "ROLE.md"))
	if err != nil {
		return ""
	}
	for _, ln := range strings.Split(string(b), "\n") {
		if t := strings.TrimSpace(ln); t != "" && !strings.HasPrefix(strings.TrimLeft(ln, " \t"), "#") {
			return head(oneline(t), 160)
		}
	}
	return ""
}

// noView is the state word of an agent the tick has stored no view for (x-state-words in protocol.json).
const noView = "no_terminal"

// viewOf is the stored view of an agent (the Python daemon's tick keeps it in agent_view) with the fields read beside it when x is given (agents.list, agent.get). An agent with no
// stored view yet gets what can be read without the tick's judgement of its screen: no terminal is known for it, so its
// state is the spec's word for that.
func (e *Env) viewOf(name string, stored map[string]json.RawMessage, x *viewCtx) (json.RawMessage, error) {
	view, err := e.viewMap(name, stored, x)
	if err != nil {
		return nil, err
	}
	return json.Marshal(view)
}

func (e *Env) viewMap(name string, stored map[string]json.RawMessage, x *viewCtx) (map[string]any, error) {
	v, err := e.baseViewOf(name, stored)
	if err != nil {
		return nil, err
	}
	dec := json.NewDecoder(bytes.NewReader(v))
	dec.UseNumber()
	var view map[string]any
	if err := dec.Decode(&view); err != nil {
		return nil, err
	}
	// A structured agent says so: a client shows its transcript where a terminal agent's terminal would be. Absent means a terminal agent.
	if e.Structured != nil && e.Structured(name) {
		view["structured"] = true
	}
	if x != nil {
		if err := e.addFields(name, view, x); err != nil {
			return nil, err
		}
	}
	return view, nil
}

func (e *Env) baseViewOf(name string, stored map[string]json.RawMessage) (json.RawMessage, error) {
	if v, ok := stored[name]; ok {
		return v, nil
	}
	view := map[string]any{
		"name": name, "role": e.roleLine(name), "model": nil, "effort": nil, "state": noView, "state_detail": "",
		"state_since": nil, "block": nil, "wall_reason": nil, "background_since": nil, "would_wake": false,
		"blocker": nil, "account": nil, "task": nil, "cache_until": nil, "has_dialog": false, "parent": nil,
	}
	cur, err := queryOne(e.DB, "SELECT t.id, t.title FROM agent_state a JOIN tasks t ON t.id = a.current_task WHERE a.agent = ?", name)
	if err != nil {
		return nil, err
	}
	if cur != nil {
		view["task"] = map[string]any{"id": cur.i("id"), "title": cur.str("title")}
	}
	return json.Marshal(view)
}

func (e *Env) storedViews() (map[string]json.RawMessage, error) {
	rows, err := query(e.DB, "SELECT agent, view FROM agent_view ORDER BY agent")
	if err != nil {
		return nil, err
	}
	out := make(map[string]json.RawMessage, len(rows))
	for _, r := range rows {
		out[r.str("agent")] = json.RawMessage(r.str("view"))
	}
	return out, nil
}

func (e *Env) agentsList(c *rpc.Conn, params map[string]any) (any, error) {
	status, ok := params["status"].(bool)
	if _, has := params["status"]; has && !ok {
		return nil, badParam("status is true or false", "status")
	}
	stored, err := e.storedViews()
	if err != nil {
		return nil, err
	}
	x, err := e.viewCtx()
	if err != nil {
		return nil, err
	}
	roster := team.NewRoster(e.Home)
	agents := []map[string]any{}
	for _, n := range e.listAgents() {
		v, err := e.viewMap(n, stored, x)
		if err != nil {
			return nil, err
		}
		if status {
			if err := e.addStatusLine(roster, n, v); err != nil {
				return nil, err
			}
		}
		agents = append(agents, v)
	}
	return map[string]any{"agents": agents}, nil
}

// addStatusLine adds what hl team draws of an agent beyond its view (rpc_agents._status_line). The screen word is read back from the
// view's state, as agent.strip reads it.
func (e *Env) addStatusLine(roster *team.Roster, name string, view map[string]any) error {
	row, err := db.GetAgentState(e.DB, name)
	if err != nil {
		return err
	}
	cfg, _ := roster.Read(name) // an unreadable agent.yaml is doctor's to report
	view["started"], view["has_repo"], view["has_terminal"] = row != nil, truthy(cfg["repo"]), row != nil && row.Terminal.String != ""
	view["screen_word"], view["screen_detail"], view["unreached"], view["task_waits"], view["wake_wait_reason"] = nil, "", 0, []any{}, nil
	if row == nil {
		return nil
	}
	state, _ := view["state"].(string)
	detail, _ := view["state_detail"].(string)
	view["screen_word"], view["screen_detail"] = screenWord(state, detail)
	if view["unreached"], err = db.UnreachedCount(e.DB, name); err != nil {
		return err
	}
	if view["task_waits"], err = e.taskWaits(name); err != nil {
		return err
	}
	if e.Delivery == nil {
		return nil
	}
	t, err := e.Delivery.Trigger(e.DB, name, row, row.Account.String)
	if err == nil && !t.Applies && t.Reason != "" {
		view["wake_wait_reason"] = t.Reason
	}
	return err
}

// taskWaits is the reason the tick recorded for each open task the agent owns that waits, oldest first (taskholds.agent_waits).
func (e *Env) taskWaits(name string) ([]any, error) {
	rows, err := query(e.DB, "SELECT t.id, m.value FROM tasks t JOIN meta m ON m.name = 'taskhold:' || t.id WHERE t.owner = ? AND "+
		db.OpenSQL+" ORDER BY t.id", name)
	if err != nil {
		return nil, err
	}
	out := []any{}
	for _, r := range rows {
		if why, ok := decodeObject(r.str("value"))["why"].(string); ok {
			out = append(out, map[string]any{"task": r.i("id"), "words": why})
		}
	}
	return out, nil
}

func (e *Env) named(params map[string]any) (string, error) {
	name, ok := params["agent"].(string)
	if !ok || name == "" {
		return "", badParam("agent is a name", "agent")
	}
	for _, n := range e.listAgents() {
		if n == name {
			return name, nil
		}
	}
	err := rpc.NotFoundError("no such agent: " + name)
	err.Data = map[string]any{"what": "agent", "name": name}
	return "", err
}

func (e *Env) agentGet(c *rpc.Conn, params map[string]any) (any, error) {
	name, err := e.named(params)
	if err != nil {
		return nil, err
	}
	stored, err := e.storedViews()
	if err != nil {
		return nil, err
	}
	x, err := e.viewCtx()
	if err != nil {
		return nil, err
	}
	raw, err := e.viewOf(name, stored, x)
	if err != nil {
		return nil, err
	}
	out := map[string]any{}
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, err
	}
	st, err := queryOne(e.DB, "SELECT worktree, terminal, resume_session, closed_at FROM agent_state WHERE agent = ?", name)
	if err != nil {
		return nil, err
	}
	out["worktree"] = nil
	if path := st.str("worktree"); path != "" {
		out["worktree"] = map[string]any{"path": path, "branch": branchOf(path)}
	}
	out["terminal"] = st.strOrNil("terminal")
	out["session_id"] = st.strOrNil("resume_session")
	if e.Structured != nil && e.Structured(name) {
		// No terminal and no typing: the session is open while its process runs, and nobody holds an input that is not served.
		out["session_open"] = e.SessionOpen != nil && e.SessionOpen(name)
		out["input_holders"] = []any{}
	} else {
		out["session_open"] = st.set("terminal") && st.str("terminal") != "" && st.i("closed_at") == 0
		if out["input_holders"], err = e.inputHolders(name); err != nil {
			return nil, err
		}
	}
	out["account_pick"] = map[string]any{"account": nil, "reason": "this daemon does not place sessions yet"}
	if out["prompt"], err = e.openPrompt(name); err != nil {
		return nil, err
	}
	return out, nil
}

// branchOf is the branch checked out at path, or "" when git cannot say.
func branchOf(path string) string {
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, "git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD").Output()
	if err != nil {
		return ""
	}
	return oneline(string(out))
}

// defaultHoldIdleS is how long a holder may go without typing when config.yaml's input_hold_idle_s does not say.
const defaultHoldIdleS = 600

// inputHolders is the spec's InputHolder list: whoever holds the input, else the daemon. A hold idle past the limit
// (config.yaml's input_hold_idle_s, else 600 seconds) no longer counts; reading does not delete it, as the Python daemon does.
func (e *Env) inputHolders(name string) ([]any, error) {
	daemon := []any{map[string]any{"kind": "daemon", "name": "daemon", "since": nil}}
	r, err := queryOne(e.DB, "SELECT kind, name, since, last_typed FROM input_holders WHERE agent = ?", name)
	if err != nil || r == nil {
		return daemon, err
	}
	idle := (&lifecycle.Settings{Home: e.Home}).ConfigSeconds("input_hold_idle_s", defaultHoldIdleS)
	if time.Now().Unix()-r.i("last_typed") >= idle {
		return daemon, nil
	}
	return []any{map[string]any{"kind": r.str("kind"), "name": r.str("name"), "since": ts(r["since"])}}, nil
}

const promptStaleS = 10

var escOption = map[string]any{"key": "esc", "label": "Cancel (Esc)", "answer": "other", "highlighted": false}

func cancelOption() map[string]any {
	o := map[string]any{}
	for k, v := range escOption {
		o[k] = v
	}
	return o
}

// optionsOf is the choices prompt.answer takes as choice, by what the screen read.
func optionsOf(kind string, answerable bool, detail map[string]any) []any {
	switch kind {
	case "permission":
		if !answerable {
			return []any{}
		}
		if detail["structured"] == true { // a structured session's: allow or deny, nothing on a screen to escape from
			return []any{
				map[string]any{"key": "allow", "label": "Allow", "answer": "yes", "highlighted": false},
				map[string]any{"key": "deny", "label": "Deny", "answer": "no", "highlighted": false}}
		}
		return []any{
			map[string]any{"key": "yes", "label": "Yes", "answer": "yes", "highlighted": false},
			map[string]any{"key": "no", "label": "No", "answer": "no", "highlighted": false}, cancelOption()}
	case "question":
		out := []any{}
		opts, _ := detail["options"].([]any)
		for i, o := range opts {
			om, _ := o.(map[string]any)
			checked, _ := om["checked"].(bool)
			label, _ := om["label"].(string)
			out = append(out, map[string]any{"key": fmt.Sprint(i + 1), "label": label, "answer": "other", "highlighted": checked})
		}
		return append(out, cancelOption())
	}
	out := []any{}
	opts, _ := detail["options"].([]any)
	for _, o := range opts {
		pair, _ := o.([]any)
		if len(pair) < 2 {
			continue
		}
		out = append(out, map[string]any{"key": fmt.Sprint(pair[0]), "label": fmt.Sprint(pair[1]), "answer": "other", "highlighted": false})
	}
	return append(out, cancelOption())
}

// truthy is Python's bool() of a decoded JSON value.
func truthy(v any) bool {
	switch x := v.(type) {
	case nil:
		return false
	case bool:
		return x
	case string:
		return x != ""
	case []any:
		return len(x) > 0
	case map[string]any:
		return len(x) > 0
	}
	if n, ok := rpc.AsInt(v); ok {
		return n != 0
	}
	return true
}

func orNil(m map[string]any, k string) any {
	if s, ok := m[k].(string); ok && s != "" {
		return s
	}
	return nil
}

// OpenPrompt is openPrompt for the code that answers one: the Prompt a stale answer carries.
func (e *Env) OpenPrompt(name string) (any, error) { return e.openPrompt(name) }

// openPrompt is the agent's open prompt as the spec's Prompt, while the screen watch has refreshed it within promptStaleS.
func (e *Env) openPrompt(name string) (any, error) {
	r, err := queryOne(e.DB, "SELECT * FROM prompt_blocks WHERE agent = ? AND gone_at IS NULL", name)
	if err != nil || r == nil || time.Now().Unix()-r.i("last_seen") > promptStaleS {
		return nil, err
	}
	return promptOf(name, r), nil
}

// promptOf is a prompt_blocks row as the spec's Prompt.
func promptOf(name string, r row) map[string]any {
	detail := decodeObject(r.str("detail"))
	var panel []any
	json.Unmarshal([]byte(r.str("panel")), &panel)
	if panel == nil {
		panel = []any{}
	}
	since := r["first_seen"]
	if since == nil || r.i("first_seen") == 0 {
		since = r["last_seen"]
	}
	kind := r.str("kind")
	if kind == "dialog" {
		kind = promptKind(detail)
	}
	return map[string]any{
		"id": r.str("fingerprint"), "agent": name, "kind": kind, "tool": orNil(detail, "tool"),
		"command": orNil(detail, "command"), "reason": orNil(detail, "reason"), "lines": panel,
		"options": optionsOf(r.str("kind"), r.i("answerable") != 0, detail), "since": ts(since),
		"multi": truthy(detail["multi_select"]),
	}
}

// promptKind is promptparse.dialog_kind: an exit confirmation is told by an option that exits.
func promptKind(detail map[string]any) string {
	var labels []string
	opts, _ := detail["options"].([]any)
	for _, o := range opts {
		if pair, _ := o.([]any); len(pair) >= 2 {
			labels = append(labels, fmt.Sprint(pair[1]))
		}
	}
	return term.DialogKind(labels)
}

func (e *Env) agentTimeline(c *rpc.Conn, params map[string]any) (any, error) {
	limit := int64(defaultLimit)
	if v, has := params["limit"]; has {
		n, ok := rpc.AsInt(v)
		if !ok || n < 1 {
			return nil, badParam("limit is a positive integer", "limit")
		}
		limit = n
	}
	before, err := optInt(params, "before")
	if err != nil {
		return nil, badParam("before is an event id", "before")
	}
	if limit > maxLimit {
		limit = maxLimit
	}
	name, err := e.named(params)
	if err != nil {
		return nil, err
	}
	var b any
	if before != nil {
		b = *before
	}
	rows, err := query(e.DB, "SELECT * FROM events WHERE agent = ? AND (? IS NULL OR id < ?) ORDER BY id DESC LIMIT ?",
		name, b, b, limit+1)
	if err != nil {
		return nil, err
	}
	items := []any{}
	for i, r := range rows {
		if int64(i) >= limit {
			break
		}
		items = append(items, timelineEntry(r))
	}
	return map[string]any{"items": items, "more": int64(len(rows)) > limit}, nil
}

func timelineEntry(r row) map[string]any {
	payload := map[string]any{}
	if p := r.str("payload"); strings.HasPrefix(p, "{") {
		payload = decodeObject(p)
	}
	var actor any
	if a, ok := payload["actor"].(map[string]any); ok {
		actor = a
	}
	text := r.str("kind")
	for _, k := range []string{"text", "detail", "method"} {
		if s := pyStr(payload[k]); s != "" {
			text = s
			break
		}
	}
	return map[string]any{"id": r.i("id"), "at": ts(r["ts"]), "kind": r.str("kind"), "actor": actor, "text": text, "data": payload}
}

// pyStr is str(v) for a payload value that counts as set (Python's `or` chain skips null, "", 0, false and empty containers).
func pyStr(v any) string {
	switch t := v.(type) {
	case nil:
		return ""
	case string:
		return t
	case bool:
		if t {
			return "True"
		}
		return ""
	case json.Number:
		if f, err := t.Float64(); err == nil && f == 0 {
			return ""
		}
		return t.String()
	case []any:
		if len(t) == 0 {
			return ""
		}
	case map[string]any:
		if len(t) == 0 {
			return ""
		}
	}
	b, _ := json.Marshal(v)
	return string(b)
}
