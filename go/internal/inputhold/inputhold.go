// Package inputhold is who types at an agent's terminal (handline/daemon/inputhold.py): the daemon unless a person or a device
// took it. While someone else holds it the daemon types nothing and presses nothing there. A holder who has not typed for
// `input_hold_idle_s` loses the hold, found out whenever the hold is read.
//
// A change of holder (a take, a release, an idle loss) is an `input_holder` event of the agent and, through Changed, a `holder`
// notice to whoever watches its terminal.
package inputhold

import (
	"database/sql"
	"os"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/yamlcompat"
)

const (
	idleKey      = "input_hold_idle_s"
	defaultIdleS = 600
	// HeldBy is in a refusal's words, so a caller that must tell it from another refusal can match it.
	HeldBy = " is held by "
)

// HeldWhy is what the daemon's refusal to type or press at a held terminal says.
func HeldWhy(agent, name string) string { return agent + "'s terminal" + HeldBy + name }

// IdleSeconds is the idle limit from config.yaml: a positive integer, else the default.
func IdleSeconds(home string) int64 {
	b, err := os.ReadFile(paths.ConfigFile(home))
	if err != nil {
		return defaultIdleS
	}
	data, err := yamlcompat.Parse(string(b))
	m, _ := data.(map[string]any)
	if err != nil {
		return defaultIdleS
	}
	switch v := m[idleKey].(type) {
	case int64:
		if v > 0 {
			return v
		}
	case int:
		if v > 0 {
			return int64(v)
		}
	}
	return defaultIdleS
}

// Changed is told, after the event is written, whenever the holder of agent's input has changed; the terminal channel sets it.
var Changed func(agent string, holders []map[string]any)

// Daemon is the holder when no person or device holds: the spec's InputHolder.
func daemonHolder() map[string]any {
	return map[string]any{"kind": "daemon", "name": "daemon", "since": nil}
}

func stamp(epoch int64) string { return time.Unix(epoch, 0).UTC().Format("2006-01-02T15:04:05Z") }

// View is the spec's InputHolder list: the person or device holding, else the daemon.
func View(q *sql.DB, home, agent string, now time.Time) ([]map[string]any, error) {
	r, err := Holder(q, home, agent, now)
	if err != nil {
		return nil, err
	}
	if r == nil {
		return []map[string]any{daemonHolder()}, nil
	}
	return []map[string]any{{"kind": r.Kind, "name": r.Name, "since": stamp(r.Since)}}, nil
}

func changed(q *sql.DB, home, agent string, now time.Time) error {
	holders, err := View(q, home, agent, now)
	if err != nil {
		return err
	}
	if _, err := db.AddEvent(q, agent, "input_holder", db.KV{K: "holders", V: holders}); err != nil {
		return err
	}
	if Changed != nil {
		Changed(agent, holders)
	}
	return nil
}

// Take holds agent's input as name: one statement that sets the row only when there is none or it is already name's, and says
// whether it did, so two takes at once cannot both win. An idle hold is dropped first.
func Take(q *sql.DB, home, agent, kind, name string, now time.Time) (bool, error) {
	if _, err := Holder(q, home, agent, now); err != nil {
		return false, err
	}
	res, err := q.Exec("INSERT INTO input_holders(agent, kind, name, since, last_typed) VALUES(?,?,?,?,?) "+
		"ON CONFLICT(agent) DO UPDATE SET last_typed = excluded.last_typed WHERE input_holders.name = excluded.name",
		agent, kind, name, now.Unix(), now.Unix())
	if err != nil {
		return false, err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return false, nil
	}
	return true, changed(q, home, agent, now)
}

// Release gives agent's input back to the daemon.
func Release(q *sql.DB, home, agent string, now time.Time) error {
	if _, err := q.Exec("DELETE FROM input_holders WHERE agent = ?", agent); err != nil {
		return err
	}
	return changed(q, home, agent, now)
}

// Row is whoever holds an agent's input.
type Row struct {
	Kind, Name string
	Since      int64
}

// Holder is whoever holds agent's input, or nil when the daemon does; an idle hold is dropped here.
func Holder(q *sql.DB, home, agent string, now time.Time) (*Row, error) {
	var r Row
	var last int64
	err := q.QueryRow("SELECT kind, name, since, last_typed FROM input_holders WHERE agent = ?", agent).Scan(&r.Kind, &r.Name, &r.Since, &last)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if now.Unix()-last >= IdleSeconds(home) {
		if _, err := q.Exec("DELETE FROM input_holders WHERE agent = ?", agent); err != nil {
			return nil, err
		}
		return nil, changed(q, home, agent, now)
	}
	return &r, nil
}

// Touch is the holder typing: the idle hold counts from now.
func Touch(q *sql.DB, agent, name string, now time.Time) error {
	_, err := q.Exec("UPDATE input_holders SET last_typed = ? WHERE agent = ? AND name = ?", now.Unix(), agent, name)
	return err
}
