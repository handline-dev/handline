package supervisor

import (
	"database/sql"
	"fmt"
	"sort"
	"sync"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/lock"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
)

// Bounds the telling layer waits by (infra/waits.py).
const (
	EndWaitS         = 60
	WakeSessionWaitS = 15
)

// Env is what the telling layer runs on: the database, the home, a log and the ports. The process memory the Python modules kept in
// module globals (what the sweep saw, the hold reasons, the text last typed) lives here.
type Env struct {
	DB   *sql.DB
	Home string
	Log  func(format string, args ...any)
	Ports
	Now   func() time.Time
	Sleep func(time.Duration)

	mu        sync.Mutex
	sendMu    sync.Mutex // the send ledger is read and written under it, so two callers never write one operation twice
	seen      map[string]Seen
	seenAt    time.Time
	drawn     map[string]drawnAt
	typedText map[string]string // handle -> what send last typed, until markTyped records it
	typedAt   map[int64]int64   // request -> when this daemon last typed it (landing)
	held      map[string]string
	noted     map[string]noted
	failed    map[string]failure
}

// Seen is what the sweep saw of one agent's session this tick (observe.Observed).
type Seen struct {
	SessionPID  int
	HasPID      bool
	SessionSeen bool // whether the process table could be read
}

type drawnAt struct {
	at  time.Time
	yes bool
}

type noted struct {
	why         string
	at          int64
	contestable bool
}

type failure struct {
	why string
	at  int64
}

func (e *Env) now() int64 {
	if e.Now != nil {
		return e.Now().Unix()
	}
	return time.Now().Unix()
}

func (e *Env) nowT() time.Time {
	if e.Now != nil {
		return e.Now()
	}
	return time.Now()
}

func (e *Env) sleep(d time.Duration) {
	if e.Sleep != nil {
		e.Sleep(d)
		return
	}
	time.Sleep(d)
}

func (e *Env) log(format string, args ...any) {
	if e.Log != nil {
		e.Log(format, args...)
	}
}

func (e *Env) init() {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.seen == nil {
		e.seen = map[string]Seen{}
		e.drawn = map[string]drawnAt{}
		e.typedText = map[string]string{}
		e.typedAt = map[int64]int64{}
		e.held = map[string]string{}
		e.noted = map[string]noted{}
		e.failed = map[string]failure{}
	}
}

// NoteSeen records what the tick's sweep saw, for the sends that follow within SeenFreshS.
func (e *Env) NoteSeen(seen map[string]Seen) {
	e.init()
	e.mu.Lock()
	e.seen, e.seenAt = seen, e.nowT()
	e.mu.Unlock()
}

// agents is the registered agents (paths.list_agents).
func (e *Env) agents() []string { return paths.ListAgents(e.Home) }

func (e *Env) registered(name string) bool {
	for _, a := range e.agents() {
		if a == name {
			return true
		}
	}
	return false
}

// withTerminal runs fn holding the agent's terminal; false when someone else holds it (lock.terminal).
func (e *Env) withTerminal(agent string, fn func()) bool {
	h := lock.TakeTerminal(e.Home, agent)
	if h == nil {
		return false
	}
	defer h.Release()
	fn()
	return true
}

// dbFailure carries a database error up through the ported code, which reads like the Python it follows; Catch turns it back.
type dbFailure struct{ err error }

// Catch is deferred by every exported method that touches the database: *err gets the failure a statement raised.
func Catch(err *error) {
	switch r := recover().(type) {
	case nil:
	case dbFailure:
		*err = r.err
	default:
		panic(r)
	}
}

func check(err error) {
	if err != nil {
		panic(dbFailure{fmt.Errorf("database: %w", err)})
	}
}

func must[T any](v T, err error) T {
	check(err)
	return v
}

// ok2 is must for the (value, found) pairs of the db package.
func ok2[T any](v T, found bool, err error) (T, bool) {
	check(err)
	return v, found
}

// stateOf is db.get_agent_state with the failure raised.
func stateOf(q db.Q, agent string) *db.AgentState { return must(db.GetAgentState(q, agent)) }

// guard is deferred by what runs after a commit, where a failed statement is logged and said, not raised: the row is written and the tick is the net.
func (e *Env) guard(what string, out *string) {
	switch r := recover().(type) {
	case nil:
	case dbFailure:
		e.log("  %s failed: %v", what, r.err)
		*out = "  " + what + " failed: " + r.err.Error()
	default:
		panic(r)
	}
}

func sortStrings(xs []string) { sort.Strings(xs) }

// idsOf is the first column of each row as an id.
func idsOf(q db.Q, query string, args ...any) ([]int64, error) {
	rows, err := q.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []int64
	for rows.Next() {
		var id int64
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		out = append(out, id)
	}
	return out, rows.Err()
}

// pyQuote is a string as Python's json.dumps writes it (non-ASCII escaped).
func pyQuote(s string) string { return rpc.PyJSON(s, false, true) }
