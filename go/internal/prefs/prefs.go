// Package prefs keeps each person's view preferences (contract Prefs): prefs.get, prefs.set, and the cleanup when an agent goes or is
// renamed. The watcher's Prefs detector tells the prefs topic.
package prefs

import (
	"database/sql"
	"encoding/json"
	"slices"
	"time"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
)

// Nav is NavPrefs.
type Nav struct {
	Pins       []string `json:"pins"`
	UnreadOnly bool     `json:"unread_only"`
	Hidden     []string `json:"hidden"`
	Layout     string   `json:"layout"`
	Collapsed  []string `json:"collapsed"`
	Order      []string `json:"order"`
}

var (
	dots    = []string{"needs", "working", "waking", "idle", "sleeping"}
	layouts = []string{"tree", "flat"}
)

func defaults() Nav {
	return Nav{Pins: []string{}, Hidden: []string{}, Layout: "tree", Collapsed: []string{}, Order: []string{}}
}

type querier interface {
	QueryRow(string, ...any) *sql.Row
}

// Of is the Prefs kept for person, the defaults when none are.
func Of(q querier, person string) (map[string]any, error) {
	nav, err := navOf(q, person)
	if err != nil {
		return nil, err
	}
	return map[string]any{"nav": nav}, nil
}

func navOf(q querier, person string) (Nav, error) {
	n := defaults()
	var raw string
	err := q.QueryRow("SELECT nav FROM prefs WHERE person = ?", person).Scan(&raw)
	if err == sql.ErrNoRows {
		return n, nil
	}
	if err != nil {
		return n, err
	}
	return n, json.Unmarshal([]byte(raw), &n)
}

func save(tx *sql.Tx, person string, n Nav) error {
	b, err := json.Marshal(n)
	if err != nil {
		return err
	}
	_, err = tx.Exec("INSERT INTO prefs(person, nav, updated_at) VALUES(?, ?, ?) "+
		"ON CONFLICT(person) DO UPDATE SET nav = excluded.nav, updated_at = excluded.updated_at", person, string(b), time.Now().Unix())
	return err
}

// Register adds prefs.get and prefs.set.
func Register(s *rpc.Server, home string) {
	s.Register("prefs.get", rpc.Method{Role: "member", Handler: func(c *rpc.Conn, _ map[string]any) (any, error) {
		return Of(c.Server().DB, c.Caller.Actor())
	}})
	s.Register("prefs.set", rpc.Method{Role: "member", Write: true, Handler: func(c *rpc.Conn, p map[string]any) (any, error) {
		changes, _ := p["changes"].(map[string]any)
		nav, ok := changes["nav"].(map[string]any)
		if !ok {
			return nil, bad("changes.nav is an object of NavPrefs keys", "changes.nav")
		}
		return c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			n, err := navOf(tx, c.Caller.Actor())
			if err != nil {
				return nil, err
			}
			if err := apply(&n, nav, paths.ListAgents(home)); err != nil {
				return nil, err
			}
			if err := save(tx, c.Caller.Actor(), n); err != nil {
				return nil, err
			}
			return map[string]any{"accepted": true, "said": "prefs saved"}, nil
		})
	}})
}

func bad(message, field string) *rpc.Error {
	return rpc.BadRequestError(message, field, rpc.InvalidParams)
}

// apply sets each key of changes on n, checked.
func apply(n *Nav, changes map[string]any, agents []string) error {
	for k, v := range changes {
		field := "changes.nav." + k
		switch k {
		case "pins", "collapsed", "order":
			names, ok := uniqueNames(v)
			if !ok {
				return bad(field+" is a list of agent names, none twice", field)
			}
			for _, name := range names {
				if !slices.Contains(agents, name) {
					return bad(field+" names "+name+", which is no agent", field)
				}
			}
			switch k {
			case "pins":
				n.Pins = names
			case "collapsed":
				n.Collapsed = names
			default:
				n.Order = names
			}
		case "hidden":
			names, ok := uniqueNames(v)
			for _, d := range names {
				ok = ok && slices.Contains(dots, d)
			}
			if !ok {
				return bad(field+" is a list of needs, working, waking, idle and sleeping, none twice", field)
			}
			n.Hidden = names
		case "unread_only":
			b, ok := v.(bool)
			if !ok {
				return bad(field+" is true or false", field)
			}
			n.UnreadOnly = b
		case "layout":
			s, _ := v.(string)
			if !slices.Contains(layouts, s) {
				return bad(field+" is tree or flat", field)
			}
			n.Layout = s
		default:
			return bad("changes.nav has no "+k, field)
		}
	}
	return nil
}

func uniqueNames(v any) ([]string, bool) {
	list, ok := v.([]any)
	if !ok {
		return nil, false
	}
	out := []string{}
	for _, x := range list {
		s, ok := x.(string)
		if !ok || s == "" || slices.Contains(out, s) {
			return nil, false
		}
		out = append(out, s)
	}
	return out, true
}

// Forget drops agent from every person's pins, folds and order.
func Forget(tx *sql.Tx, agent string) error {
	return each(tx, func(names []string) []string {
		return slices.DeleteFunc(names, func(s string) bool { return s == agent })
	})
}

// Rename makes old new in every person's pins, folds and order, in place.
func Rename(tx *sql.Tx, old, new string) error {
	return each(tx, func(names []string) []string {
		for i, s := range names {
			if s == old {
				names[i] = new
			}
		}
		return names
	})
}

// each rewrites the pins, folds and order of every person, storing only what changed.
func each(tx *sql.Tx, edit func([]string) []string) error {
	rows, err := tx.Query("SELECT person FROM prefs")
	if err != nil {
		return err
	}
	var people []string
	for rows.Next() {
		var p string
		if err := rows.Scan(&p); err != nil {
			rows.Close()
			return err
		}
		people = append(people, p)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return err
	}
	for _, p := range people {
		n, err := navOf(tx, p)
		if err != nil {
			return err
		}
		pins, folds, order := slices.Clone(n.Pins), slices.Clone(n.Collapsed), slices.Clone(n.Order)
		n.Pins, n.Collapsed, n.Order = edit(n.Pins), edit(n.Collapsed), edit(n.Order)
		if slices.Equal(pins, n.Pins) && slices.Equal(folds, n.Collapsed) && slices.Equal(order, n.Order) {
			continue
		}
		if err := save(tx, p, n); err != nil {
			return err
		}
	}
	return nil
}
