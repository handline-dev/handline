package usage

import (
	"database/sql"
	"encoding/json"
	"errors"
	"sort"

	"github.com/handline/handline/go/internal/db"
)

// What a cold start costs, from what the daemon watches happen (handline/usage/measure.py): the share of the destination's shortest
// window one new session spends getting from nothing to the end of its first turn. A measurement is taken only when nothing else was
// spending the account's window at either end, and the first turn is timed whole, so it is an upper bound on the start itself.

const (
	// ColdKeep recent measurements are looked at, and ColdMinSamples are needed before they stand in for the starting value.
	ColdKeep       = 10
	ColdMinSamples = 3
)

func coldKey(agent string) string { return "coldstart:" + agent }

// ForgetColdStart drops an open measurement, when the start it was for did not happen.
func ForgetColdStart(q db.Q, agent string) error { return db.SetMeta(q, coldKey(agent), "") }

type openStart struct {
	Account string `json:"account"`
	Before  int    `json:"before"`
	At      int64  `json:"at"`
}

// BeginColdStart is measure.begin: a session has just been started for agent on acct from nothing.
func (e *Env) BeginColdStart(q db.Q, agent, acct string) error {
	pct := e.Read(acct).ShortTrusted()
	if pct == nil {
		return ForgetColdStart(q, agent)
	}
	others, err := e.ActiveAgents(q, agent)
	if err != nil {
		return err
	}
	if others[acct] > 0 {
		return ForgetColdStart(q, agent)
	}
	b, err := json.Marshal(openStart{acct, *pct, int64(e.now())})
	if err != nil {
		return err
	}
	return db.SetMeta(q, coldKey(agent), string(b))
}

// SettleColdStart is measure.settle: close a measurement once the agent's first turn has ended. A reading that fell, a missing one or
// another agent at work on the account means nothing is recorded.
func (e *Env) SettleColdStart(q db.Q, agent string) error {
	raw, _, err := db.Meta(q, coldKey(agent))
	if err != nil || raw == "" {
		return err
	}
	var m openStart
	if json.Unmarshal([]byte(raw), &m) != nil || m.Account == "" {
		return ForgetColdStart(q, agent)
	}
	var one int
	err = q.QueryRow("SELECT 1 FROM events WHERE agent = ? AND kind = 'Stop' AND ts >= ? LIMIT 1", agent, m.At).Scan(&one)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil // the first turn has not ended
		}
		return err
	}
	if err := ForgetColdStart(q, agent); err != nil {
		return err
	}
	after := e.Read(m.Account).ShortTrusted()
	others, err := e.ActiveAgents(q, agent)
	if err != nil {
		return err
	}
	if after == nil || others[m.Account] > 0 || *after < m.Before {
		return nil
	}
	_, err = db.AddEventAt(q, int64(e.now()), agent, "cold_start", db.KV{K: "account", V: m.Account}, db.KV{K: "before", V: m.Before},
		db.KV{K: "after", V: *after}, db.KV{K: "pct", V: *after - m.Before})
	return err
}

// ColdStartPct is measure.cold_start_pct: the middle of the recent measurements, ok false while there are too few.
func ColdStartPct(q db.Q) (float64, bool, error) {
	rows, err := q.Query("SELECT payload FROM events WHERE kind = 'cold_start' ORDER BY id DESC LIMIT ?", ColdKeep)
	if err != nil {
		return 0, false, err
	}
	defer rows.Close()
	var vals []float64
	for rows.Next() {
		var payload *string
		if err := rows.Scan(&payload); err != nil {
			return 0, false, err
		}
		var p map[string]any
		if payload != nil && decodeObject(*payload, &p) {
			if f, ok := num(p["pct"]); ok {
				vals = append(vals, f)
			}
		}
	}
	if err := rows.Err(); err != nil {
		return 0, false, err
	}
	if len(vals) < ColdMinSamples {
		return 0, false, nil
	}
	sort.Float64s(vals)
	return median(vals), true, nil
}
