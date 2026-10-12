// Package prompts is the one prompt an agent's screen shows, kept as a row the bridge reads and answers
// (handline/actions/prompt_blocks.py, dialogs.py, confirmations.py, ask_question.py, daemon/prompts.py, daemon/rpc_prompts.py).
//
// The screen watch judges each agent's screen once and keeps a prompt_blocks row for what it shows: a permission prompt, an
// AskUserQuestion, or a plain dialog, never two at once, in that priority. Answering presses keys only for what a fresh read of
// the screen shows now: a prompt whose panel text, or (for a permission prompt) hook wait id, no longer matches the row is refused.
package prompts

import (
	"crypto/sha1"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"github.com/handline/handline/go/internal/inputhold"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/hooks"
	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/term"
)

// StaleS is how old a row's last_seen may be before it no longer counts as a prompt on screen: a watch that stopped, or a screen
// it cannot read, must not leave a prompt showing for good.
const StaleS = 10

// Env is what the prompt code works on.
type Env struct {
	DB    *sql.DB
	Home  string
	Term  *term.Term
	Now   func() time.Time
	Trust *team.Trust
	// Reads draws the Prompt a stale answer carries.
	Reads *reads.Env
	// Structured answers the prompts of agents that run as structured sessions; nil when there are none.
	Structured Structured
	// Holds is who holds each terminal's input; nil leaves an answer ungated.
	Holds *inputhold.Store
}

func (e *Env) now() time.Time {
	if e.Now != nil {
		return e.Now()
	}
	return time.Now()
}

// Block is a prompt_blocks row, panel and detail decoded.
type Block struct {
	ID                  int64
	Agent, Kind         string
	Fingerprint, Title  string
	Panel               []string
	Detail              map[string]any
	WaitID              sql.NullInt64
	Answerable          bool
	FirstSeen, LastSeen int64
	GoneAt              sql.NullInt64
}

const blockCols = "id, agent, kind, fingerprint, title, panel, detail, wait_id, answerable, first_seen, last_seen, gone_at"

func scanBlock(r interface{ Scan(...any) error }) (*Block, error) {
	var b Block
	var panel, detail string
	var answerable int64
	if err := r.Scan(&b.ID, &b.Agent, &b.Kind, &b.Fingerprint, &b.Title, &panel, &detail, &b.WaitID, &answerable,
		&b.FirstSeen, &b.LastSeen, &b.GoneAt); err != nil {
		return nil, err
	}
	b.Answerable = answerable != 0
	json.Unmarshal([]byte(panel), &b.Panel)
	json.Unmarshal([]byte(detail), &b.Detail)
	if b.Panel == nil {
		b.Panel = []string{}
	}
	if b.Detail == nil {
		b.Detail = map[string]any{}
	}
	return &b, nil
}

// OpenOf is agent's open row (db.open_prompt), refreshed or not; nil when there is none.
func (e *Env) OpenOf(agent string) (*Block, error) {
	b, err := scanBlock(e.DB.QueryRow("SELECT "+blockCols+" FROM prompt_blocks WHERE agent = ? AND gone_at IS NULL", agent))
	if err == sql.ErrNoRows {
		return nil, nil
	}
	return b, err
}

// OpenBlockOf is agent's open block, when the watch has refreshed it within StaleS.
func (e *Env) OpenBlockOf(agent string) (*Block, error) {
	b, err := e.OpenOf(agent)
	if err != nil || b == nil || e.now().Unix()-b.LastSeen > StaleS {
		return nil, err
	}
	return b, nil
}

func (e *Env) get(id int64) (*Block, error) {
	b, err := scanBlock(e.DB.QueryRow("SELECT "+blockCols+" FROM prompt_blocks WHERE id = ?", id))
	if err == sql.ErrNoRows {
		return nil, nil
	}
	return b, err
}

// Fingerprint is the identity of what a screen block says: its kind and panel text.
func Fingerprint(kind string, panel []string) string {
	sum := sha1.Sum([]byte(strings.Join(append([]string{kind}, panel...), "\n")))
	return hex.EncodeToString(sum[:])
}

// Seen is what the watch read off a screen, as it keeps it.
type Seen struct {
	Kind, Title string
	Panel       []string
	Detail      map[string]any
	WaitID      sql.NullInt64
	Answerable  bool
}

// vendorOf is the vendor an agent runs under; an unreadable agent file is not a dialog, and reads as the default.
func (e *Env) vendorOf(agent string) string {
	cfg, err := team.NewRoster(e.Home).Read(agent)
	if err != nil {
		return term.DefaultVendor
	}
	if v, _ := cfg["vendor"].(string); v != "" {
		return v
	}
	return term.DefaultVendor
}

func onScreen(text string, lines []string) bool {
	return text != "" && strings.Contains(strings.Join(strings.Fields(strings.Join(lines, " ")), " "), strings.Join(strings.Fields(text), " "))
}

func str(m map[string]any, k string) string { s, _ := m[k].(string); return s }

// describe is what term.PromptOnScreen read, as the row keeps it. held is the hook's record of the wait, or nil to read it now.
func (e *Env) describe(agent, kind string, c *term.Confirmation, q *term.Question, d *term.Dialog, held map[string]any) Seen {
	switch kind {
	case term.PromptPermission:
		if held == nil {
			held = hooks.ReadWait(e.Home, agent)
		}
		if held == nil {
			held = map[string]any{}
		}
		command, reason := str(held, "command"), str(held, "reason")
		seen := term.PermissionFields(c.Title, c.Lines)
		tool := str(held, "tool")
		if tool == "" {
			tool = seen.Tool
		}
		if !onScreen(command, c.Lines) {
			command = seen.Command
		}
		if !onScreen(reason, c.Lines) {
			reason = seen.Reason
		}
		wait, has := hooks.WaitID(held)
		detail := map[string]any{"agent": agent, "vendor": e.vendorOf(agent), "tool": tool, "title": c.Title, "lines": strs(c.Lines),
			"command": command, "reason": reason, "since": held["since"], "wait": nil}
		s := Seen{Kind: kind, Title: c.Title, Panel: strs(c.Lines), Detail: detail}
		if has {
			detail["wait"] = wait
			// A permission prompt with no hook record has nothing to pin its identity to, so it cannot be answered from here: the
			// terminal is where it is answered.
			s.WaitID, s.Answerable = sql.NullInt64{Int64: wait, Valid: true}, true
		}
		return s
	case term.PromptQuestion:
		if held == nil {
			held = hooks.ReadWait(e.Home, agent)
		}
		title := q.Question
		if title == "" {
			title = q.Header
		}
		return Seen{Kind: kind, Title: title, Panel: strs(q.Lines), Detail: e.questionDict(agent, q, held), Answerable: true}
	}
	opts := make([]any, len(d.Options))
	for i, o := range d.Options {
		opts[i] = []any{o.Number, o.Label}
	}
	detail := map[string]any{"agent": agent, "options": opts, "body": term.Context(d), "truncated": d.Title == term.TruncatedTitle}
	return Seen{Kind: kind, Title: d.Title, Panel: strs(d.Body), Detail: detail, Answerable: true}
}

func strs(in []string) []string { return append([]string{}, in...) }

// questionData is the hook's own record of the tool call, preferred for display over the screen's own parse since it is Claude's
// own words with nothing to mis-OCR, but only when its option count and multiSelect flag still match the screen: a stale record
// would otherwise show the founder the wrong words for the right buttons.
func questionData(held map[string]any, q *term.Question) map[string]any {
	data, _ := held["question_data"].(map[string]any)
	questions, _ := data["questions"].([]any)
	if len(questions) != 1 {
		return nil
	}
	q0, _ := questions[0].(map[string]any)
	options, _ := q0["options"].([]any)
	if q0 == nil || len(options) != len(q.Options) {
		return nil
	}
	if truthy(q0["multiSelect"]) != q.MultiSelect {
		return nil
	}
	return q0
}

func truthy(v any) bool {
	switch x := v.(type) {
	case nil:
		return false
	case bool:
		return x
	case string:
		return x != ""
	case float64:
		return x != 0
	case []any:
		return len(x) > 0
	case map[string]any:
		return len(x) > 0
	}
	return true
}

func orStr(a any, fallback string) string {
	if s, _ := a.(string); s != "" {
		return s
	}
	return fallback
}

func (e *Env) questionDict(agent string, q *term.Question, held map[string]any) map[string]any {
	q0 := questionData(held, q)
	opts := make([]any, len(q.Options))
	for i, o := range q.Options {
		label, desc := o.Label, o.Description
		if q0 != nil {
			om, _ := q0["options"].([]any)[i].(map[string]any)
			label, desc = "", ""
			if s, _ := om["label"].(string); s != "" {
				label = s
			}
			if s, _ := om["description"].(string); s != "" {
				desc = s
			}
		}
		opts[i] = map[string]any{"label": label, "description": desc, "checked": o.Checked}
	}
	header, question := q.Header, q.Question
	if q0 != nil {
		header, question = orStr(q0["header"], header), orStr(q0["question"], question)
	}
	var otherText any
	if q.HasOtherText {
		otherText = q.OtherText
	}
	return map[string]any{"agent": agent, "vendor": e.vendorOf(agent), "header": header, "question": question, "options": opts,
		"multi_select": q.MultiSelect, "other_number": q.OtherNumber, "other_text": otherText, "lines": strs(q.Lines)}
}

// StaleError is the refusal for an answer to a prompt that is not the one open now; current is that one, or nil.
func StaleError(current any) *rpc.Error {
	return &rpc.Error{RPCCode: -32003, Code: "stale_prompt", Message: "the prompt on screen is not the one answered; nothing was sent",
		Data: map[string]any{"current": current}}
}

// New is the prompt code of one daemon: the terminal in force, read by the vendor of the agent recorded in each terminal.
func New(db *sql.DB, home string, rd *reads.Env) *Env {
	e := &Env{DB: db, Home: home, Trust: team.NewTrust(home), Reads: rd}
	e.Term = term.New(term.Dynamic(home), e.driverFor)
	return e
}

// driverFor is the driver of the agent recorded in this terminal, or nil for one the daemon did not launch (the default
// vendor's is used then) or whose vendor has none.
func (e *Env) driverFor(handle string) term.Driver {
	var agent string
	if err := e.DB.QueryRow("SELECT agent FROM agent_state WHERE terminal = ? ORDER BY updated DESC LIMIT 1", handle).Scan(&agent); err != nil {
		return nil
	}
	return term.DriverFor(e.vendorOf(agent))
}
