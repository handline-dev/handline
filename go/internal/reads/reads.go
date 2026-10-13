// Package reads serves the daemon's read methods from the same sqlite database the Python daemon uses:
// daemon.status, agents.list, agent.get, agent.timeline, tasks.list, task.get and inbox.list. Nothing here writes.
// Each method follows its Python one (handline/daemon/rpc_status.py, rpc_agents.py, rpc_taskview.py, rpc_inbox.py)
// and the rows they read; a test builds a database with the Python migrations' schema and holds the answers to them.
package reads

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"github.com/handline/handline/go/internal/weblogin"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
	"unicode"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/team"
)

const (
	defaultLimit = 50
	maxLimit     = 200
)

// Env is what the methods read: the open database, the handline home, when this daemon started.
type Env struct {
	DB      *sql.DB
	Home    string
	Started time.Time
	Sha     string
	Schema  int
	// Structured says whether the daemon runs the agent as a structured session; nil is none. transcript.events is served for those only.
	Structured func(agent string) bool
	// SessionOpen says whether a structured agent's session is running; nil is none. A structured agent has no terminal, so agent.get
	// reads this where a terminal agent's row says it.
	SessionOpen func(agent string) bool
	// Context is the model id and the context figures of an agent on an account, as the usage reads give them: the status file's percent,
	// tokens used (nil when unknown or stale) and the limit tokens are judged against (0 when unknown). agent.strip leaves them null then.
	Context func(agent, account string) (model string, pct *int, used *int64, limit int)
	// Login is an account's latest web login, nil for none; nil is no logins.
	Login func(account string) *weblogin.Login
	// Delivery is what the supervisor knows of why a queued line waits; nil leaves out the reasons that need it.
	Delivery Delivery
	// LoginState is whether an account has credentials, known false when this process cannot tell; nil asks the machine
	// (team.Accounts).
	LoginState func(account string) (loggedIn, known bool)
}

// Register adds the read methods to the server's table.
func Register(s *rpc.Server, env Env) {
	e := &env
	s.Register("daemon.status", rpc.Method{Role: "agent", Handler: e.status})
	s.Register("agents.list", rpc.Method{Role: "agent", Handler: e.agentsList})
	s.Register("agent.get", rpc.Method{Role: "agent", Handler: e.agentGet})
	s.Register("agent.strip", rpc.Method{Role: "member", Handler: e.agentStrip})
	s.Register("agent.timeline", rpc.Method{Role: "member", Handler: e.agentTimeline})
	s.Register("tasks.list", rpc.Method{Role: "agent", Handler: e.tasksList})
	s.Register("task.get", rpc.Method{Role: "agent", Handler: e.taskGet})
	s.Register("events.feed", rpc.Method{Role: "agent", Handler: e.eventsFeed})
	s.Register("prompt.list", rpc.Method{Role: "member", Handler: e.promptList})
	s.Register("settings.get", rpc.Method{Role: "founder", Handler: e.settingsGet})
	s.Register("answers.list", rpc.Method{Role: "founder", Handler: e.answersList})
	s.Register("inbox.get", rpc.Method{Role: "agent", Handler: e.inboxGet, Waits: true})
	s.Register("request.read", rpc.Method{Role: "agent", Handler: e.requestRead, Waits: true})
	s.Register("chat.messages", rpc.Method{Role: "member", Handler: e.chatMessages})
	s.Register("chat.requests", rpc.Method{Role: "member", Handler: e.chatRequests})
	s.Register("chat.transcript", rpc.Method{Role: "member", Handler: e.chatTranscript})
	s.Register("transcript.events", rpc.Method{Role: "member", Handler: e.transcriptEvents})
	s.Register("chat.locate", rpc.Method{Role: "member", Handler: e.chatLocate})
	s.Register("chat.refs", rpc.Method{Role: "member", Handler: e.chatRefs})
	s.Register("chat.search", rpc.Method{Role: "member", Handler: e.chatSearch})
	s.Register("hr.list", rpc.Method{Role: "agent", Handler: e.hrList})
	s.Register("repos.list", rpc.Method{Role: "member", Handler: e.reposList})
	s.Register("worktrees.list", rpc.Method{Role: "member", Handler: e.worktreesList})
	s.Register("worktree.diff", rpc.Method{Role: "member", Handler: e.worktreeDiff})
	s.Register("agent.options", rpc.Method{Role: "member", Handler: e.agentOptions})
	s.Register("operation.get", rpc.Method{Role: "member", Handler: e.operationGet})
	s.Register("accounts.list", rpc.Method{Role: "agent", Handler: e.accountsList})
	s.Register("status.pick", rpc.Method{Role: "agent", Handler: e.statusPick})
	s.Register("stats.get", rpc.Method{Role: "member", Handler: e.statsGet})
	s.Register("stats.outlook", rpc.Method{Role: "member", Handler: e.statsOutlook})
	s.Register("team.get", rpc.Method{Role: "agent", Handler: e.teamGet})
	s.Register("agent.triggers", rpc.Method{Role: "agent", Handler: e.agentTriggers})
	s.Register("schedules.list", rpc.Method{Role: "agent", Handler: e.schedulesList})
	s.Register("inbox.list", rpc.Method{Role: "agent", Handler: e.inboxList, Waits: true})
}

// row is one result row by column name, the way sqlite3.Row reads in the Python daemon.
type row map[string]any

func query(db *sql.DB, q string, args ...any) ([]row, error) {
	rs, err := db.Query(q, args...)
	if err != nil {
		return nil, err
	}
	defer rs.Close()
	cols, err := rs.Columns()
	if err != nil {
		return nil, err
	}
	var out []row
	for rs.Next() {
		vals := make([]any, len(cols))
		ptrs := make([]any, len(cols))
		for i := range vals {
			ptrs[i] = &vals[i]
		}
		if err := rs.Scan(ptrs...); err != nil {
			return nil, err
		}
		r := row{}
		for i, c := range cols {
			if b, ok := vals[i].([]byte); ok {
				vals[i] = string(b)
			}
			r[c] = vals[i]
		}
		out = append(out, r)
	}
	return out, rs.Err()
}

func queryOne(db *sql.DB, q string, args ...any) (row, error) {
	rs, err := query(db, q, args...)
	if err != nil || len(rs) == 0 {
		return nil, err
	}
	return rs[0], nil
}

func (r row) set(k string) bool { return r != nil && r[k] != nil }

func (r row) str(k string) string {
	if v, ok := r[k].(string); ok {
		return v
	}
	return ""
}

// strOrNil is the column as JSON has it: a string, or null.
func (r row) strOrNil(k string) any {
	if v, ok := r[k].(string); ok {
		return v
	}
	return nil
}

func (r row) i(k string) int64 { return num(r[k]) }

// num is a sqlite number as a whole one; 0 for null.
func num(v any) int64 {
	switch n := v.(type) {
	case int64:
		return n
	case float64:
		return int64(n)
	}
	return 0
}

func (r row) intOrNil(k string) any {
	if r[k] == nil {
		return nil
	}
	return r.i(k)
}

// ts is the spec's Timestamp: whole seconds, UTC; null for a column that is null.
func ts(v any) any {
	switch n := v.(type) {
	case int64:
		return time.Unix(n, 0).UTC().Format("2006-01-02T15:04:05Z")
	case float64:
		return time.Unix(int64(n), 0).UTC().Format("2006-01-02T15:04:05Z")
	}
	return nil
}

// Param readers: a wrong type is a bad_request naming the field, as the Python methods' helpers do.

func badParam(msg, field string) error { return rpc.BadRequestError(msg, field, rpc.InvalidParams) }

func optInt(params map[string]any, key string) (*int64, error) {
	v, has := params[key]
	if !has || v == nil {
		return nil, nil
	}
	n, ok := rpc.AsInt(v)
	if !ok {
		return nil, badParam(key+" is an integer", key)
	}
	return &n, nil
}

func optStr(params map[string]any, key string) (*string, error) {
	v, has := params[key]
	if !has || v == nil {
		return nil, nil
	}
	s, ok := v.(string)
	if !ok {
		return nil, badParam(key+" is a string", key)
	}
	return &s, nil
}

func optBool(params map[string]any, key string) (bool, error) {
	v, has := params[key]
	if !has {
		return false, nil
	}
	b, ok := v.(bool)
	if !ok {
		return false, badParam(key+" is true or false", key)
	}
	return b, nil
}

// pageLimit is limit from 1 to maxLimit, defaultLimit when absent.
func pageLimit(params map[string]any) (int, error) {
	n, err := optInt(params, "limit")
	if err != nil {
		return 0, badParam(fmt.Sprintf("limit is an integer from 1 to %d", maxLimit), "limit")
	}
	if n == nil {
		return defaultLimit, nil
	}
	if *n < 1 || *n > maxLimit {
		return 0, badParam(fmt.Sprintf("limit is an integer from 1 to %d", maxLimit), "limit")
	}
	return int(*n), nil
}

// listAgents is the registered agents, as paths.list_agents has them: a directory holding agent.yaml, by name.
func (e *Env) listAgents() []string {
	entries, err := os.ReadDir(filepath.Join(e.Home, "agents"))
	if err != nil {
		return []string{}
	}
	out := []string{}
	for _, d := range entries {
		if _, err := os.Stat(filepath.Join(e.Home, "agents", d.Name(), "agent.yaml")); err == nil {
			out = append(out, d.Name())
		}
	}
	sort.Strings(out)
	return out
}

// oneline is a value as one line of printable text.
func oneline(s string) string {
	s = strings.Join(strings.FieldsFunc(s, func(r rune) bool {
		return r == '\n' || r == '\r' || r == '\v' || r == '\f' || r == 0x85 || r == 0x2028 || r == 0x2029
	}), " ")
	return strings.Map(func(r rune) rune {
		if unicode.IsPrint(r) {
			return r
		}
		return ' '
	}, s)
}

// head is s cut to n characters.
func head(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[:n])
	}
	return s
}

func decodeObject(s string) map[string]any {
	d := json.NewDecoder(strings.NewReader(s))
	d.UseNumber()
	var m map[string]any
	if d.Decode(&m) != nil || m == nil {
		return map[string]any{}
	}
	return m
}

// loginState is an account's login as the daemon's login check answers it.
func (e *Env) loginState(account string) (loggedIn, known bool) {
	if e.LoginState != nil {
		return e.LoginState(account)
	}
	return team.Accounts{Home: e.Home}.LoginState(account)
}
