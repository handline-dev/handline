// Package inputhold is who types at an agent's terminal (handline/daemon/inputhold.py): the daemon unless a person or a device
// took it. While someone else holds it the daemon types nothing and presses nothing there. A holder who has not typed for
// `input_hold_idle_s` loses the hold, found out whenever the hold is read.
//
// Only what a press needs is here: who holds, the refusal's words, and the holder's keystroke. Taking, releasing and the events
// are the terminal channel's.
package inputhold

import (
	"database/sql"
	"os"
	"time"

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

// Row is whoever holds an agent's input.
type Row struct {
	Kind, Name string
	Since      int64
}

// Holder is whoever holds agent's input, or nil when the daemon does. An idle hold is dropped here, and dropped says so: the loss is
// recorded and told to the terminal's watchers like a take, which only the caller can do, so a caller that gets dropped runs that step
// (termchan.Env.HoldersChanged).
func Holder(db *sql.DB, home, agent string, now time.Time) (*Row, bool, error) {
	var r Row
	var last int64
	err := db.QueryRow("SELECT kind, name, since, last_typed FROM input_holders WHERE agent = ?", agent).Scan(&r.Kind, &r.Name, &r.Since, &last)
	if err == sql.ErrNoRows {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, err
	}
	if now.Unix()-last < IdleSeconds(home) {
		return &r, false, nil
	}
	res, err := db.Exec("DELETE FROM input_holders WHERE agent = ?", agent)
	if err != nil {
		return nil, false, err
	}
	n, err := res.RowsAffected() // two readers may find it idle together: only the one that deleted the row tells
	return nil, n > 0, err
}

// Touch is the holder typing: the idle hold counts from now.
func Touch(db *sql.DB, agent, name string, now time.Time) error {
	_, err := db.Exec("UPDATE input_holders SET last_typed = ? WHERE agent = ? AND name = ?", now.Unix(), agent, name)
	return err
}

// Take holds agent's input as name: one statement that sets the row only when there is none or it is already name's, and says
// whether it did, so two takes at once cannot both win. The caller reads Holder first, which drops an idle hold and tells of it.
func Take(db *sql.DB, agent, kind, name string, now time.Time) (bool, error) {
	res, err := db.Exec("INSERT INTO input_holders(agent, kind, name, since, last_typed) VALUES(?,?,?,?,?) "+
		"ON CONFLICT(agent) DO UPDATE SET last_typed = excluded.last_typed WHERE input_holders.name = excluded.name",
		agent, kind, name, now.Unix(), now.Unix())
	if err != nil {
		return false, err
	}
	n, err := res.RowsAffected()
	return n > 0, err
}

// Release gives agent's input back to the daemon.
func Release(db *sql.DB, agent string) error {
	_, err := db.Exec("DELETE FROM input_holders WHERE agent = ?", agent)
	return err
}
