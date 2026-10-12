// Package inputhold is who types at an agent's terminal: the daemon unless a person or a device took it (input.take)
// (handline/daemon/inputhold.py). A holder who has not typed for the idle limit loses the hold, found out whenever it is
// read, and the loss is recorded like a take. The row is the Python daemon's own (input_holders).
package inputhold

import (
	"database/sql"
	"time"

	"github.com/handline/handline/go/internal/db"
)

// DefaultIdleS is how long a holder may go without typing when config.yaml's input_hold_idle_s does not say.
const DefaultIdleS = 600

// HeldBy is the words between an agent's terminal and who holds it, in a refusal.
const HeldBy = " is held by "

// HeldWhy is what the daemon's refusal to type or press at a held terminal says.
func HeldWhy(agent, name string) string { return agent + "'s terminal" + HeldBy + name }

// Row is a hold: who, and since when.
type Row struct {
	Kind, Name string
	Since      int64
	LastTyped  int64
}

// Store is the holds of one daemon.
type Store struct {
	DB *sql.DB
	// IdleS is the idle limit in seconds; nil is DefaultIdleS.
	IdleS func() int64
	// Changed is told the new holders wherever a hold is found to have changed, an idle expiry included.
	Changed func(agent string, holders []map[string]any)
	Now     func() time.Time
}

func (s *Store) now() int64 {
	if s.Now != nil {
		return s.Now().Unix()
	}
	return time.Now().Unix()
}

func (s *Store) idle() int64 {
	if s.IdleS != nil {
		return s.IdleS()
	}
	return DefaultIdleS
}

func stamp(t int64) any { return time.Unix(t, 0).UTC().Format("2006-01-02T15:04:05Z") }

// Daemon is the holder list of a terminal nobody took.
func Daemon() []map[string]any {
	return []map[string]any{{"kind": "daemon", "name": "daemon", "since": nil}}
}

func (s *Store) changed(agent string) error {
	h, err := s.View(agent)
	if err != nil {
		return err
	}
	if _, err := db.AddEvent(s.DB, agent, "input_holder", db.KV{K: "holders", V: h}); err != nil {
		return err
	}
	if s.Changed != nil {
		s.Changed(agent, h)
	}
	return nil
}

// Holder is the row of whoever holds agent's input, nil when the daemon does; an idle hold is dropped here.
func (s *Store) Holder(agent string) (*Row, error) {
	r := &Row{}
	err := s.DB.QueryRow("SELECT kind, name, since, last_typed FROM input_holders WHERE agent = ?", agent).Scan(&r.Kind, &r.Name, &r.Since, &r.LastTyped)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if s.now()-r.LastTyped >= s.idle() {
		if _, err := s.DB.Exec("DELETE FROM input_holders WHERE agent = ?", agent); err != nil {
			return nil, err
		}
		return nil, s.changed(agent)
	}
	return r, nil
}

// View is the spec's InputHolder list: the person or device holding, else the daemon.
func (s *Store) View(agent string) ([]map[string]any, error) {
	r, err := s.peek(agent)
	if err != nil || r == nil {
		return Daemon(), err
	}
	return []map[string]any{{"kind": r.Kind, "name": r.Name, "since": stamp(r.Since)}}, nil
}

// peek is Holder without the idle drop's own notice: View is what the drop tells, so it must not drop again.
func (s *Store) peek(agent string) (*Row, error) {
	r := &Row{}
	err := s.DB.QueryRow("SELECT kind, name, since, last_typed FROM input_holders WHERE agent = ?", agent).Scan(&r.Kind, &r.Name, &r.Since, &r.LastTyped)
	if err == sql.ErrNoRows || (err == nil && s.now()-r.LastTyped >= s.idle()) {
		return nil, nil
	}
	return r, err
}

// Take holds agent's input as name: the row is set only when there is none or it is already name's, and it says whether it was,
// so two takes at once cannot both win.
func (s *Store) Take(agent, kind, name string) (bool, error) {
	if _, err := s.Holder(agent); err != nil {
		return false, err
	}
	now := s.now()
	res, err := s.DB.Exec("INSERT INTO input_holders(agent, kind, name, since, last_typed) VALUES(?,?,?,?,?) "+
		"ON CONFLICT(agent) DO UPDATE SET last_typed = excluded.last_typed WHERE input_holders.name = excluded.name",
		agent, kind, name, now, now)
	if err != nil {
		return false, err
	}
	n, _ := res.RowsAffected()
	if n > 0 {
		return true, s.changed(agent)
	}
	return false, nil
}

// Release gives the input back to the daemon.
func (s *Store) Release(agent string) error {
	if _, err := s.DB.Exec("DELETE FROM input_holders WHERE agent = ?", agent); err != nil {
		return err
	}
	return s.changed(agent)
}

// Touch counts the holder's typing for the idle hold.
func (s *Store) Touch(agent, name string) error {
	_, err := s.DB.Exec("UPDATE input_holders SET last_typed = ? WHERE agent = ? AND name = ?", s.now(), agent, name)
	return err
}

// HeldByOther is the name of whoever holds the input against the daemon's own typing, "" when nobody does.
func (s *Store) HeldByOther(agent string) (string, error) {
	r, err := s.Holder(agent)
	if r == nil || err != nil {
		return "", err
	}
	return r.Name, nil
}
