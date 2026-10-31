package watcher

import (
	"database/sql"

	"github.com/handline/handline/go/internal/prefs"
	"github.com/handline/handline/go/internal/stream"
)

// Prefs is the prefs topic: prefs.changed with the whole Prefs whenever a person's row changed.
type Prefs struct {
	seen map[string]string
}

func (*Prefs) Name() string { return "prefs" }

func (*Prefs) Tables() []string { return []string{"prefs"} }

func (p *Prefs) Scan(q *sql.DB, emit Emit) error {
	rows, err := q.Query("SELECT person, nav FROM prefs ORDER BY person")
	if err != nil {
		return err
	}
	now := map[string]string{}
	var people []string
	for rows.Next() {
		var person, nav string
		if err := rows.Scan(&person, &nav); err != nil {
			rows.Close()
			return err
		}
		now[person] = nav
		people = append(people, person)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return err
	}
	before := p.seen
	p.seen = now
	if before == nil {
		return nil
	}
	for _, person := range people {
		if before[person] == now[person] {
			continue
		}
		data, err := prefs.Of(q, person)
		if err != nil {
			return err
		}
		if _, err := emit(stream.Entry{Topic: "prefs", Type: "prefs.changed", Data: data}); err != nil {
			return err
		}
	}
	return nil
}
