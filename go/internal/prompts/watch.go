package prompts

import (
	"database/sql"
	"time"

	"github.com/handline/handline/go/internal/hooks"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/term"
)

// The screen watch: what prompt each agent's screen shows, kept as a prompt_blocks row. A job of its own, apart from the tick: it
// does nothing but read screens, so it can run every couple of seconds without dragging the tick's slower work with it. Each pass
// reads every agent's screen once and reconciles the result with the agent's open row, level-triggered: the same prompt refreshes
// last_seen, a different one closes the old row and opens a new one, none closes it. A screen that cannot be read changes nothing:
// a row is never closed or refreshed on a guess, and a row nobody has refreshed for StaleS is not shown, so a stopped watch fails
// closed. Nothing here decides anything about the agent; it does not press keys.
const WatchS = 2 * time.Second

func jsonText(v any) string { return rpc.PyJSON(v, true, true) }

// watched is the terminal to read for this agent, or "": none, closed, or the daemon's own /exit dialog (delivery_blocks origin
// 'close') which the tick already handles.
func (e *Env) watched(agent string, terminal sql.NullString, closed sql.NullInt64) (string, error) {
	if terminal.String == "" || (closed.Valid && closed.Int64 != 0) {
		return "", nil
	}
	var origin string
	err := e.DB.QueryRow("SELECT origin FROM delivery_blocks WHERE agent = ?", agent).Scan(&origin)
	if err == nil && origin == "close" {
		return "", nil
	}
	if err != nil && err != sql.ErrNoRows {
		return "", err
	}
	return terminal.String, nil
}

// WatchAgent brings agent's open row in line with its screen; it says what it did, for the log.
func (e *Env) WatchAgent(agent, handle string, now int64) (string, error) {
	kind, conf, q, d := e.Term.PromptOnScreen(handle)
	if kind == term.PromptUnreadable {
		return "unreadable", nil
	}
	open, err := e.OpenOf(agent)
	if err != nil {
		return "", err
	}
	if kind == term.PromptNone {
		if open != nil {
			return "closed", e.closeRow(open.ID, now)
		}
		return "none", nil
	}
	seen := e.describe(agent, kind, conf, q, d, hooks.ReadWait(e.Home, agent))
	fp := Fingerprint(seen.Kind, seen.Panel)
	if open != nil {
		if open.Fingerprint == fp && open.WaitID == seen.WaitID {
			_, err := e.DB.Exec("UPDATE prompt_blocks SET last_seen = ? WHERE id = ?", now, open.ID)
			return "seen", err
		}
		if err := e.closeRow(open.ID, now); err != nil {
			return "", err
		}
	}
	answerable := 0
	if seen.Answerable {
		answerable = 1
	}
	var wait any
	if seen.WaitID.Valid {
		wait = seen.WaitID.Int64
	}
	_, err = e.DB.Exec("INSERT INTO prompt_blocks(agent, kind, fingerprint, title, panel, detail, wait_id, answerable, first_seen, last_seen) "+
		"VALUES(?,?,?,?,?,?,?,?,?,?)", agent, seen.Kind, fp, seen.Title, jsonText(seen.Panel), jsonText(seen.Detail), wait, answerable, now, now)
	return "opened", err
}

func (e *Env) closeRow(id, now int64) error {
	_, err := e.DB.Exec("UPDATE prompt_blocks SET gone_at = ? WHERE id = ? AND gone_at IS NULL", now, id)
	return err
}

// WatchOnce is one pass over every agent; the seconds it took.
func (e *Env) WatchOnce(log func(string, ...any)) (time.Duration, error) {
	started := e.now()
	type agentRow struct {
		agent    string
		terminal sql.NullString
		closed   sql.NullInt64
	}
	rows, err := e.DB.Query("SELECT agent, terminal, closed_at FROM agent_state ORDER BY agent")
	if err != nil {
		return 0, err
	}
	var all []agentRow
	for rows.Next() {
		var r agentRow
		if err := rows.Scan(&r.agent, &r.terminal, &r.closed); err != nil {
			rows.Close()
			return 0, err
		}
		all = append(all, r)
	}
	rows.Close()
	for _, r := range all {
		handle, err := e.watched(r.agent, r.terminal, r.closed)
		if err != nil {
			return 0, err
		}
		if handle == "" {
			if open, err := e.OpenOf(r.agent); err == nil && open != nil { // its terminal is gone: so is the prompt
				e.closeRow(open.ID, started.Unix())
			}
			continue
		}
		did, err := e.WatchAgent(r.agent, handle, started.Unix())
		if err != nil { // one agent's screen is not the pass's failure
			log("prompts: %s: %v", r.agent, err)
			continue
		}
		if did == "opened" || did == "closed" {
			log("prompts: %s: %s", r.agent, did)
		}
	}
	return time.Since(started), nil
}

// Run loops until stop closes. It waits the longer of WatchS and the pass's own length, so a slow host is read from at most half
// the time however many agents there are.
func (e *Env) Run(stop <-chan struct{}, log func(string, ...any)) {
	for {
		took, err := e.WatchOnce(log)
		if err != nil { // a bad pass is not the end of the watch
			log("prompts: pass failed: %v", err)
			took = 0
		}
		wait := max(WatchS, took)
		select {
		case <-stop:
			return
		case <-time.After(wait):
		}
	}
}
