package tick

import (
	"database/sql"
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
)

// An account whose login expired: marked from a terminal's screen, its agents moved off it. Claude Code draws "Login expired · Please run
// /login" when a turn is refused, and fires no hook the daemon reads: the agent stays RUNNING on an account that can run nothing, and the
// account still looks logged in, since its credential is there. The screen is the only witness, so it is read here and the account is
// marked, which is what stops launches onto it and makes the bridge offer a re-login. The move is level-triggered on that mark, not on the
// screen: every agent on a marked account leaves it.
const (
	LoginLine = "Login expired · Please run /login"
	// LoginScreenRows: only the rows nearest the prompt count, and only a row that is nothing but the line: a quote of it in prose, or in
	// this team's own talk about it, is not it.
	LoginScreenRows = 14
	loginBullets    = "⏺●"
	// LoginCheckEveryS: one screen read per agent this often; a read costs a call to the host.
	LoginCheckEveryS = 20
	// LoginFreshSessionS: a session this young has its own screen on; a line left over from the one before is not its.
	LoginFreshSessionS = 120
	// LoginStall is what a stall raised here says, and what Lift looks for.
	LoginStall = "login expired"
	// LoginTrigger is handoff.LOGIN_TRIGGER.
	LoginTrigger = "login"
)

// LoginMark is accounts.login_expired: why an account is marked logged out.
type LoginMark struct{ Why string }

// LoginAccounts is team/accounts.py as the login check uses it.
type LoginAccounts interface {
	// LoginExpired is the account's mark, nil when it has none.
	LoginExpired(acct string) *LoginMark
	// MarkLoginExpired marks the account; true when this call made the mark.
	MarkLoginExpired(acct, why string) bool
	// CurrentAccount is agents.current_account.
	CurrentAccount(q db.Q, agent string) string
}

// LoginExpired is the login check, with the per-agent time of its last screen read.
type LoginExpired struct {
	Accounts LoginAccounts
	checked  map[string]int64
}

// LoginShown is whether the screen text ends with a row that is nothing but the bullet and the line.
func LoginShown(text string) bool {
	var rows []string
	for _, r := range strings.Split(text, "\n") {
		if r = strings.TrimSpace(r); r != "" {
			rows = append(rows, r)
		}
	}
	if len(rows) > LoginScreenRows {
		rows = rows[len(rows)-LoginScreenRows:]
	}
	for _, r := range rows {
		first, size := firstRune(r)
		if strings.ContainsRune(loginBullets, first) && strings.TrimSpace(r[size:]) == LoginLine {
			return true
		}
	}
	return false
}

func firstRune(s string) (rune, int) {
	for i, r := range s {
		_ = i
		return r, len(string(r))
	}
	return 0, 0
}

// sessionRow is the newest session start or account move: its event id and time, 0 for none.
func sessionRow(q db.Q, agent string) (id, ts int64, err error) {
	var i, t sql.NullInt64
	err = q.QueryRow("SELECT MAX(id), MAX(ts) FROM events WHERE agent = ? AND kind IN ('SessionStart', 'moved_account')", agent).Scan(&i, &t)
	return i.Int64, t.Int64, err
}

func liftedKey(agent string) string { return "login_lifted:" + agent }

// liftedInThisSession: a stall was lifted in the session still running. Its screen keeps the line it drew before the login, and a read of
// it would mark the account again.
func liftedInThisSession(q db.Q, agent string) bool {
	_, set := must2(db.CursorUpdated(q, liftedKey(agent)))
	if !set {
		return false
	}
	id, _, err := sessionRow(q, agent)
	check(err)
	return must(db.Cursor(q, liftedKey(agent))) == id
}

// markFromScreen is the account's mark, made now when this agent's screen shows the line.
func (l *LoginExpired) markFromScreen(t *Tick, agent string, row *db.AgentState, acct string) *LoginMark {
	handle := row.Terminal.String
	now := t.Now()
	if handle == "" || now-l.checked[agent] < LoginCheckEveryS || !t.Host.TermAvailable() {
		return nil
	}
	if l.checked == nil {
		l.checked = map[string]int64{}
	}
	l.checked[agent] = now
	_, ts, err := sessionRow(t.Q, agent)
	check(err)
	if (ts != 0 && float64(now-ts) < LoginFreshSessionS) || liftedInThisSession(t.Q, agent) {
		return nil
	}
	text, ok := t.Host.ScreenText(handle)
	if !ok || !LoginShown(text) {
		return nil
	}
	why := fmt.Sprintf("%s's terminal showed \"%s\"", agent, LoginLine)
	if l.Accounts.MarkLoginExpired(acct, why) {
		check(db.AddEventForAccount(t.Q, agent, "login_expired", acct, db.KV{K: "account", V: acct}, db.KV{K: "why", V: why}))
		t.logf("  %s: login expired (%s) - marked logged out, no launch lands on it", acct, why)
	}
	return l.Accounts.LoginExpired(acct)
}

// Check is true when this agent was dealt with and the tick has nothing more to do for it.
func (l *LoginExpired) Check(t *Tick, agent string, row *db.AgentState, acct string, actions *[]string) bool {
	if l == nil || acct == "" || row == nil || closedAt(row) || row.Terminal.String == "" ||
		(row.State != states.Running && row.State != states.Idle && row.State != states.Queued) {
		return false
	}
	marked := l.Accounts.LoginExpired(acct)
	if marked == nil {
		marked = l.markFromScreen(t, agent, row, acct)
	}
	if marked == nil {
		return false
	}
	dest, why := t.Handoff.MovePlan(t.Q, agent, LoginTrigger)
	if dest == "" {
		// Nothing is waiting out a window here, so a retry loop would only fail again: the agent is shown as stopped, and Lift releases it
		// once the account logs in.
		detail := fmt.Sprintf("%s on %s and no other account can take it (%s) - hl login %s", LoginStall, acct, why, acct)
		check(db.SetAgentState(t.Q, agent, states.Stalled, 0, clip(detail, 300)))
		*actions = append(*actions, fmt.Sprintf("%s: STALLED, %s on %s", agent, LoginStall, acct))
		t.logf("  %s: %s", agent, detail)
		return true
	}
	trig := Trigger{true, LoginTrigger, fmt.Sprintf("%s on %s: %s", LoginStall, acct, marked.Why)}
	ok := t.Handoff.PerformHandoff(t.Q, agent, trig, dest)
	suffix := ""
	if !ok {
		suffix = " -> degraded"
	}
	*actions = append(*actions, fmt.Sprintf("%s: handoff (login)%s", agent, suffix))
	return true
}

// Lift ends a stall raised here once its account is logged in again.
func (l *LoginExpired) Lift(t *Tick, agent string, row *db.AgentState, actions *[]string) {
	if row.State != states.Stalled || !strings.Contains(row.Detail.String, LoginStall) {
		return
	}
	acct := l.Accounts.CurrentAccount(t.Q, agent)
	if acct != "" && l.Accounts.LoginExpired(acct) != nil {
		return
	}
	id, _, err := sessionRow(t.Q, agent)
	check(err)
	check(db.SetCursor(t.Q, liftedKey(agent), id))
	who := acct
	if who == "" {
		who = "its account"
	}
	check(db.SetAgentState(t.Q, agent, states.Idle, 0, who+" is logged in again; the stall is lifted"))
	*actions = append(*actions, fmt.Sprintf("%s: STALLED -> IDLE (%s logged in again)", agent, acct))
	t.logf("  %s: %s is logged in again; the stall is lifted", agent, acct)
}

func clip(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[:n])
	}
	return s
}
