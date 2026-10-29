// Package prefs keeps each person's view preferences (contract Prefs): prefs.get, prefs.set, and the cleanup when an agent goes or is
// renamed. The watcher's Prefs detector tells the prefs topic.
package prefs

import (
	"database/sql"
	"encoding/json"
	"maps"
	"slices"
	"strings"
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
	// Later is the For you items put off, by "kind:id".
	Later map[string]Later `json:"later,omitempty"`
}

// Later is one deferral (contract Later): the item returns at Until, or when task AfterTask is done or discarded. Exactly one is set.
type Later struct {
	Until     string `json:"until,omitempty"` // RFC 3339, as the contract's Timestamp
	AfterTask int64  `json:"after_task,omitempty"`
}

// laterKinds are the kinds of For you item that can wait: a prompt or an HR proposal holds up a running agent.
var laterKinds = []string{"task", "comment", "machine"}

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
		case "later":
			if err := applyLater(n, v, field); err != nil {
				return err
			}
		default:
			return bad("changes.nav has no "+k, field)
		}
	}
	return nil
}

// applyLater merges v, an object of "kind:id" to a deferral or null, into n.Later.
func applyLater(n *Nav, v any, field string) error {
	entries, ok := v.(map[string]any)
	if !ok {
		return bad(field+" is an object of kind:id to a deferral or null", field)
	}
	next := maps.Clone(n.Later)
	if next == nil {
		next = map[string]Later{}
	}
	for key, e := range entries {
		kind, id, _ := strings.Cut(key, ":")
		if !slices.Contains(laterKinds, kind) || id == "" {
			return bad(field+" key "+key+" is not kind:id of a For you item", field)
		}
		if e == nil {
			delete(next, key)
			continue
		}
		obj, _ := e.(map[string]any)
		until, hasUntil := obj["until"]
		after, hasAfter := obj["after_task"]
		if hasUntil == hasAfter || len(obj) != 1 {
			return bad(field+"."+key+" has exactly one of until and after_task", field+"."+key)
		}
		var l Later
		if hasUntil {
			if t, ok := timeOf(until); ok {
				l.Until = t.UTC().Format(time.RFC3339)
			}
		} else if f, ok := after.(float64); ok && f == float64(int64(f)) {
			l.AfterTask = int64(f)
		}
		if l.Until == "" && l.AfterTask <= 0 {
			return bad(field+"."+key+" is a time (until) or a task number (after_task)", field+"."+key)
		}
		next[key] = l
	}
	n.Later = next
	if len(next) == 0 {
		n.Later = nil
	}
	return nil
}

// timeOf is a Timestamp (RFC 3339).
func timeOf(v any) (time.Time, bool) {
	s, _ := v.(string)
	t, err := time.Parse(time.RFC3339, s)
	return t, err == nil
}

// Over says whether the deferral is over: its time has passed, or the task it waits for is not open (openTask answers for a task number).
func (l Later) Over(now time.Time, openTask func(int64) bool) bool {
	if l.AfterTask > 0 {
		return !openTask(l.AfterTask)
	}
	t, ok := timeOf(l.Until)
	return !ok || !t.After(now)
}

// LaterOf is the deferrals the person kept, by "kind:id".
func LaterOf(q querier, person string) (map[string]Later, error) {
	n, err := navOf(q, person)
	return n.Later, err
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
