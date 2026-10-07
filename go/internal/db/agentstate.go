package db

import (
	"database/sql"
	"fmt"
)

// AgentState is one agent_state row (db.get_agent_state).
type AgentState struct {
	Agent, State                                                        string
	WakeAt, ClosedAt, CurrentTask                                       sql.NullInt64
	Detail, Terminal, Worktree, Account, ResumeSession, Task            sql.NullString
	Handoff, AccountSource, LaunchParts, LaunchFingerprint              sql.NullString
	ExtraWorktrees, NotifiedSession                                     sql.NullString
	Updated, FailedAttempts, Continuations, ThrottleCount, WakeFailures int64
}

const stateCols = "agent, state, wake_at, detail, updated, failed_attempts, continuations, throttle_count, wake_failures, terminal, worktree, " +
	"account, closed_at, resume_session, task, handoff, account_source, launch_parts, launch_fingerprint, extra_worktrees, notified_session, current_task"

type scanner interface{ Scan(...any) error }

func scanState(r scanner) (*AgentState, error) {
	s := &AgentState{}
	err := r.Scan(&s.Agent, &s.State, &s.WakeAt, &s.Detail, &s.Updated, &s.FailedAttempts, &s.Continuations, &s.ThrottleCount, &s.WakeFailures,
		&s.Terminal, &s.Worktree, &s.Account, &s.ClosedAt, &s.ResumeSession, &s.Task, &s.Handoff, &s.AccountSource, &s.LaunchParts,
		&s.LaunchFingerprint, &s.ExtraWorktrees, &s.NotifiedSession, &s.CurrentTask)
	return s, err
}

// GetAgentState is db.get_agent_state: nil when the agent has no row.
func GetAgentState(q Q, agent string) (*AgentState, error) {
	s, err := scanState(q.QueryRow("SELECT "+stateCols+" FROM agent_state WHERE agent = ?", agent))
	if err == sql.ErrNoRows {
		return nil, nil
	}
	return s, err
}

// AllAgentStates is db.all_agent_states.
func AllAgentStates(q Q) ([]*AgentState, error) {
	return stateRows(q, "SELECT "+stateCols+" FROM agent_state ORDER BY agent")
}

// DueAgents is db.due_agents: the agents whose wake_at has come.
func DueAgents(q Q, now int64) ([]*AgentState, error) {
	return stateRows(q, "SELECT "+stateCols+" FROM agent_state WHERE wake_at IS NOT NULL AND wake_at <= ? ORDER BY wake_at", now)
}

func stateRows(q Q, query string, args ...any) ([]*AgentState, error) {
	rows, err := q.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*AgentState
	for rows.Next() {
		s, err := scanState(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, s)
	}
	return out, rows.Err()
}

func nullStr(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func nullInt(n int64, set bool) any {
	if !set {
		return nil
	}
	return n
}

// SetTask is db.set_task: the free-text prompt a spawned terminal starts on.
func SetTask(q Q, agent, task string) error {
	_, err := q.Exec("INSERT INTO agent_state(agent, state, updated, task) VALUES(?,?,?,?) ON CONFLICT(agent) DO UPDATE SET task=excluded.task, updated=excluded.updated",
		agent, "IDLE", Now(), nullStr(task))
	return err
}

// SetCurrentTask is db.set_current_task: the one board task the agent holds now. Giving it a task ends the idle spell its
// agent-level progress note was written in (T366). set false clears it.
func SetCurrentTask(q Q, agent string, tid int64, set bool) error {
	if set {
		if _, err := q.Exec("INSERT INTO meta(name, value, updated) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated=excluded.updated",
			AgentProgressKey(agent), nil, Now()); err != nil {
			return err
		}
	}
	_, err := q.Exec("INSERT INTO agent_state(agent, state, updated, current_task) VALUES(?,?,?,?) ON CONFLICT(agent) DO UPDATE SET current_task=excluded.current_task, updated=excluded.updated",
		agent, "IDLE", Now(), nullInt(tid, set))
	return err
}

// CurrentTask is db.current_task.
func CurrentTask(q Q, agent string) (int64, bool, error) {
	var n sql.NullInt64
	err := q.QueryRow("SELECT current_task FROM agent_state WHERE agent = ?", agent).Scan(&n)
	if err == sql.ErrNoRows {
		return 0, false, nil
	}
	return n.Int64, n.Valid, err
}

// SetAgentState is db.set_agent_state; wakeAt 0 and detail "" write NULL.
func SetAgentState(q Q, agent, state string, wakeAt int64, detail string) error {
	_, err := q.Exec("INSERT INTO agent_state(agent, state, wake_at, detail, updated) VALUES(?,?,?,?,?) ON CONFLICT(agent) DO UPDATE SET state=excluded.state, wake_at=excluded.wake_at, detail=excluded.detail, updated=excluded.updated",
		agent, state, nullInt(wakeAt, wakeAt != 0), nullStr(detail), Now())
	return err
}

// SetStateUnlessRunning is db.set_state_unless_running: true only when this call wrote it.
func SetStateUnlessRunning(q Q, agent, state, detail string) (bool, error) {
	res, err := q.Exec("UPDATE agent_state SET state = ?, wake_at = NULL, detail = ?, updated = ? WHERE agent = ? AND state != 'RUNNING'",
		state, nullStr(detail), Now(), agent)
	if err != nil {
		return false, err
	}
	n, _ := res.RowsAffected()
	return n > 0, nil
}

// SetTerminal is db.set_terminal: where an agent lives; a new agent is IDLE.
func SetTerminal(q Q, agent, terminal, worktree string) error {
	_, err := q.Exec("INSERT INTO agent_state(agent, state, updated, terminal, worktree) VALUES(?,?,?,?,?) ON CONFLICT(agent) DO UPDATE SET terminal=excluded.terminal, worktree=excluded.worktree, updated=excluded.updated",
		agent, "IDLE", Now(), nullStr(terminal), nullStr(worktree))
	return err
}

// SetAccount is db.set_account; source is "recorded" or "assumed".
func SetAccount(q Q, agent, account, source string) error {
	_, err := q.Exec("INSERT INTO agent_state(agent, state, updated, account, account_source) VALUES(?,?,?,?,?) ON CONFLICT(agent) DO UPDATE SET account=excluded.account, account_source=excluded.account_source, updated=excluded.updated",
		agent, "IDLE", Now(), nullStr(account), source)
	return err
}

// SetLaunchFingerprint is db.set_launch_fingerprint.
func SetLaunchFingerprint(q Q, agent, fingerprint, parts string) error {
	_, err := q.Exec("INSERT INTO agent_state(agent, state, updated, launch_fingerprint, launch_parts) VALUES(?,?,?,?,?) ON CONFLICT(agent) DO UPDATE SET launch_fingerprint=excluded.launch_fingerprint, launch_parts=excluded.launch_parts, updated=excluded.updated",
		agent, "IDLE", Now(), nullStr(fingerprint), nullStr(parts))
	return err
}

// SetClosed is db.set_closed: the session was closed, and this is how to get it back.
func SetClosed(q Q, agent, session string) error {
	_, err := q.Exec("UPDATE agent_state SET closed_at = ?, resume_session = ?, updated = ? WHERE agent = ?", Now(), nullStr(session), Now(), agent)
	return err
}

// ForgetResume is db.forget_resume: closed still, with no session to resume.
func ForgetResume(q Q, agent string) error {
	_, err := q.Exec("UPDATE agent_state SET resume_session = NULL, updated = ? WHERE agent = ?", Now(), agent)
	return err
}

// ClearClosed is db.clear_closed: a session runs again, a new process, told its queue afresh unless a tell was recorded after the close.
func ClearClosed(q Q, agent string) error {
	var closed sql.NullInt64
	err := q.QueryRow("SELECT closed_at FROM agent_state WHERE agent = ?", agent).Scan(&closed)
	if err != nil && err != sql.ErrNoRows {
		return err
	}
	told, ok, err := CursorUpdated(q, "notify:"+agent)
	if err != nil {
		return err
	}
	if !(closed.Valid && closed.Int64 != 0 && ok && told != 0 && told > closed.Int64) {
		if err := ForgetTold(q, agent); err != nil {
			return err
		}
	}
	_, err = q.Exec("UPDATE agent_state SET closed_at = NULL, resume_session = NULL, updated = ? WHERE agent = ?", Now(), agent)
	return err
}

// SetExtraWorktrees is db.set_extra_worktrees; paths is stored as JSON, NULL when empty.
func SetExtraWorktrees(q Q, agent string, paths []string) error {
	var v any
	if len(paths) > 0 {
		v = jsonList(paths)
	}
	_, err := q.Exec("INSERT INTO agent_state(agent, state, updated, extra_worktrees) VALUES(?,?,?,?) ON CONFLICT(agent) DO UPDATE SET extra_worktrees=excluded.extra_worktrees",
		agent, "IDLE", Now(), v)
	return err
}

var counters = map[string]bool{"failed_attempts": true, "continuations": true, "throttle_count": true, "wake_failures": true}

// BumpCounter is db.bump_counter; column is one of the four counters.
func BumpCounter(q Q, agent, column string, by int64) error {
	if !counters[column] {
		return fmt.Errorf("not a counter column: %s", column)
	}
	_, err := q.Exec(fmt.Sprintf("INSERT INTO agent_state(agent, state, updated, %s) VALUES(?,?,?,?) ON CONFLICT(agent) DO UPDATE SET %s = agent_state.%s + ?, updated = ?", column, column, column),
		agent, "IDLE", Now(), by, by, Now())
	return err
}

// ResetCounter is db.reset_counter.
func ResetCounter(q Q, agent, column string) error {
	if !counters[column] {
		return fmt.Errorf("not a counter column: %s", column)
	}
	_, err := q.Exec(fmt.Sprintf("UPDATE agent_state SET %s = 0 WHERE agent = ?", column), agent)
	return err
}

// Terminal is db.get_terminal.
func Terminal(q Q, agent string) (string, error) {
	var t sql.NullString
	err := q.QueryRow("SELECT terminal FROM agent_state WHERE agent = ?", agent).Scan(&t)
	if err == sql.ErrNoRows {
		return "", nil
	}
	return t.String, err
}

// AgentOfTerminal is db.agent_of_terminal: the agent last recorded in this terminal.
func AgentOfTerminal(q Q, terminal string) (string, error) {
	var a string
	err := q.QueryRow("SELECT agent FROM agent_state WHERE terminal = ? ORDER BY updated DESC LIMIT 1", terminal).Scan(&a)
	if err == sql.ErrNoRows {
		return "", nil
	}
	return a, err
}

// ActivityKinds are the events that move an agent's activity clock: its own turns and commands, never what ocp does to it.
var ActivityKinds = []string{"ask", "cancel", "commit", "raise_decision", "report", "resolve", "withdraw", "no_task",
	"task_new", "task_start", "task_note", "task_begin", "task_handoff",
	"UserPromptSubmit", "Stop", "StopFailure", "SubagentStop", "PostCompact", "PermissionRequest", "TeammateIdle",
	"stop_gate", "edit_gate"}

func marks(n int) string {
	s := ""
	for i := 0; i < n; i++ {
		if i > 0 {
			s += ","
		}
		s += "?"
	}
	return s
}

func maxInt(q Q, query string, args ...any) (int64, bool, error) {
	var n sql.NullInt64
	if err := q.QueryRow(query, args...).Scan(&n); err != nil {
		return 0, false, err
	}
	return n.Int64, n.Valid, nil
}

func kindArgs(prefix []any, kinds []string) []any {
	args := append([]any{}, prefix...)
	for _, k := range kinds {
		args = append(args, k)
	}
	return args
}

// LastActivity is db.last_activity: when the agent last did anything we can see.
func LastActivity(q Q, agent string) (int64, bool, error) {
	return maxInt(q, "SELECT MAX(ts) FROM events WHERE agent = ? AND kind IN ("+marks(len(ActivityKinds))+")", kindArgs([]any{agent}, ActivityKinds)...)
}

// LastActivityBefore is db.last_activity_before: LastActivity as of ts.
func LastActivityBefore(q Q, agent string, ts int64) (int64, bool, error) {
	return maxInt(q, "SELECT MAX(ts) FROM events WHERE agent = ? AND ts < ? AND kind IN ("+marks(len(ActivityKinds))+")", kindArgs([]any{agent, ts}, ActivityKinds)...)
}

// LastActivityID is db.last_activity_id: the id of the agent's latest event of one of kinds.
func LastActivityID(q Q, agent string, kinds []string) (int64, bool, error) {
	return maxInt(q, "SELECT MAX(id) FROM events WHERE agent = ? AND kind IN ("+marks(len(kinds))+")", kindArgs([]any{agent}, kinds)...)
}

// LastAccountActivity is db.last_account_activity: when a session on the account last took a turn.
func LastAccountActivity(q Q, account string) (int64, bool, error) {
	return maxInt(q, "SELECT MAX(ts) FROM events WHERE account = ? AND kind IN ('UserPromptSubmit', 'Stop', 'StopFailure', 'PostCompact', 'SessionStart')", account)
}

// LastEventTS is db.last_event_ts.
func LastEventTS(q Q, agent, kind string) (int64, bool, error) {
	return maxInt(q, "SELECT MAX(ts) FROM events WHERE agent = ? AND kind = ?", agent, kind)
}

// LastEventID is db.last_event_id.
func LastEventID(q Q, agent, kind string) (int64, bool, error) {
	return maxInt(q, "SELECT MAX(id) FROM events WHERE agent = ? AND kind = ?", agent, kind)
}
