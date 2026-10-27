package db

import (
	"database/sql"
	"fmt"
	"strconv"
	"strings"
)

// GetHandoff is db.get_handoff: the record, "" when there is none.
func GetHandoff(q Q, agent string) (string, error) {
	var h sql.NullString
	err := q.QueryRow("SELECT handoff FROM agent_state WHERE agent = ?", agent).Scan(&h)
	if err == sql.ErrNoRows {
		return "", nil
	}
	return h.String, err
}

// SetHandoff is db.set_handoff: keep the record. Keeping one for an agent with no row creates it idle.
func SetHandoff(q Q, agent, text string) error {
	_, err := q.Exec("INSERT INTO agent_state(agent, state, updated, handoff) VALUES(?, 'IDLE', ?, ?) ON CONFLICT(agent) DO UPDATE SET handoff = excluded.handoff", agent, Now(), text)
	return err
}

// ClearHandoff is db.set_handoff with None.
func ClearHandoff(q Q, agent string) error {
	_, err := q.Exec("UPDATE agent_state SET handoff = NULL WHERE agent = ?", agent)
	return err
}

// LastHandoffTrigger is the trigger_kind of the agent's most recently updated handoff (db.last_handoff), "" when it has none.
func LastHandoffTrigger(q Q, agent string) (string, error) {
	var k sql.NullString
	err := q.QueryRow("SELECT trigger_kind FROM handoff WHERE agent = ? ORDER BY updated DESC LIMIT 1", agent).Scan(&k)
	if err == sql.ErrNoRows {
		return "", nil
	}
	return k.String, err
}

// HandoffRow is one handoff row.
type HandoffRow struct {
	ID, Agent, Step, State string
	SessionID, TriggerKind sql.NullString
	Deadline               sql.NullInt64
	Detail                 sql.NullString
	Updated                int64
}

// InFlightHandoffs is db.in_flight_handoffs: the handoffs not yet finished, oldest update first.
func InFlightHandoffs(q Q) ([]HandoffRow, error) {
	rows, err := q.Query("SELECT id, agent, session_id, trigger_kind, step, deadline, state, detail, updated FROM handoff WHERE state = 'in_flight' ORDER BY updated")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []HandoffRow
	for rows.Next() {
		var h HandoffRow
		if err := rows.Scan(&h.ID, &h.Agent, &h.SessionID, &h.TriggerKind, &h.Step, &h.Deadline, &h.State, &h.Detail, &h.Updated); err != nil {
			return nil, err
		}
		out = append(out, h)
	}
	return out, rows.Err()
}

// AdvanceHandoff is db.advance_handoff: commit one step; an empty state or detail keeps the one there.
func AdvanceHandoff(q Q, id, step, state, detail string) error {
	_, err := q.Exec("UPDATE handoff SET step = ?, state = COALESCE(?, state), detail = COALESCE(?, detail), updated = ? WHERE id = ?",
		step, nullStr(state), nullStr(detail), Now(), id)
	return err
}

// StartHandoff is db.start_handoff: an in-flight handoff at its first step.
func StartHandoff(q Q, id, agent, sessionID, triggerKind string, deadline int64) error {
	_, err := q.Exec("INSERT OR REPLACE INTO handoff(id, agent, session_id, trigger_kind, step, deadline, state, detail, updated) VALUES(?,?,?,?,'requested',?,'in_flight',NULL,?)",
		id, agent, nullStr(sessionID), triggerKind, deadline, Now())
	return err
}

// InFlightFor is db.in_flight_for: the agent's oldest handoff still in flight, nil when none.
func InFlightFor(q Q, agent string) (*HandoffRow, error) {
	return handoffRow(q, "agent = ? AND state = 'in_flight' ORDER BY updated", agent)
}

// handoffRow is the first handoff row matching the clause after WHERE, nil when none does.
func handoffRow(q Q, where string, args ...any) (*HandoffRow, error) {
	var h HandoffRow
	err := q.QueryRow("SELECT id, agent, session_id, trigger_kind, step, deadline, state, detail, updated FROM handoff WHERE "+where+" LIMIT 1", args...).
		Scan(&h.ID, &h.Agent, &h.SessionID, &h.TriggerKind, &h.Step, &h.Deadline, &h.State, &h.Detail, &h.Updated)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	return &h, err
}

// Session starts kept per agent, and the window in which a fresh session's first hook claims one.
const (
	SessionTextMaxChars = 20000
	SessionStartsKept   = 50
	BindWindowS         = 600
)

func clip(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[:n])
	}
	return s
}

// NoteSessionStart is db.note_session_start: keep that a session was started, why and what it was given; only the oldest beyond
// SessionStartsKept go. A fresh launch has no session id yet (BindSessionStart).
func NoteSessionStart(q Q, agent, cause string, resumed bool, sessionID, text, prompt, actor string) (int64, error) {
	r := 0
	if resumed {
		r = 1
	}
	res, err := q.Exec("INSERT INTO session_starts(agent, ts, session_id, cause, resumed, text, prompt, actor) VALUES(?,?,?,?,?,?,?,?)",
		agent, Now(), nullStr(sessionID), cause, r, clip(text, SessionTextMaxChars), clip(prompt, SessionTextMaxChars), clip(actor, 80))
	if err != nil {
		return 0, err
	}
	id, _ := res.LastInsertId()
	_, err = q.Exec("DELETE FROM session_starts WHERE agent = ? AND id NOT IN (SELECT id FROM session_starts WHERE agent = ? ORDER BY id DESC LIMIT ?)", agent, agent, SessionStartsKept)
	return id, err
}

// LatestHandoff is the agent's most recently updated handoff row, nil when it has none.
func LatestHandoff(q Q, agent string) (*HandoffRow, error) {
	return handoffRow(q, "agent = ? ORDER BY updated DESC", agent)
}

// LastBoundSessionStart is when the agent's latest session start that has its session id was kept, 0 when none has: the start the
// chat shows as a New session line.
func LastBoundSessionStart(q Q, agent string) (int64, error) {
	var ts int64
	err := q.QueryRow("SELECT COALESCE(MAX(ts), 0) FROM session_starts WHERE agent = ? AND session_id IS NOT NULL", agent).Scan(&ts)
	return ts, err
}

// RestartHeld is whether nothing more is typed at the agent now: a handoff note is being written, a handoff trigger is deferred behind a
// running turn, a clear is owed, or a handoff of the agent is in flight (decided, not yet released). Each counts only for NoteWaitStaleS
// since it began, or since the row's last step, so one that stuck holds for that long at most; the reconciler gives a row up sooner, at
// its deadline.
func RestartHeld(q Q, agent string, now int64) (bool, error) {
	if _, _, kept, err := NoteWaitKept(q, agent, now); err != nil || kept {
		return kept, err
	}
	for _, key := range []string{deferredKey(agent), "clear_owed:" + agent} {
		since, err := Cursor(q, key)
		if err != nil {
			return false, err
		}
		if since != 0 && now-since <= NoteWaitStaleS {
			return true, nil
		}
	}
	h, err := InFlightFor(q, agent)
	return h != nil && now-h.Updated <= NoteWaitStaleS, err
}

func deferredKey(agent string) string { return "handoff_deferred:" + agent }

// MarkDeferred keeps that a handoff trigger waits for the agent's running turn to end, since now. The first time is kept until
// ClearDeferred: the tick fires the trigger again every pass, and a later call must not start the hold over.
func MarkDeferred(q Q, agent string, now int64) error {
	if since, err := Cursor(q, deferredKey(agent)); err != nil || since != 0 {
		return err
	}
	return SetCursor(q, deferredKey(agent), now)
}

// ClearDeferred ends the wait MarkDeferred kept: the handoff opened its row, or its trigger no longer fires.
func ClearDeferred(q Q, agent string) error { return ClearCursor(q, deferredKey(agent)) }

func noteWaitKey(agent string) string { return "handoff_note_wait:" + agent }

// NoteWaitStaleS is how long a wait for the agent's note counts as one: a trigger that went away before it was acted on, or a session that
// ended mid-wait, leaves no handoff showing and does not hold the next wait to its time.
const NoteWaitStaleS = 600

// MarkNoteWait keeps that a handoff of this kind waits for the agent's note, since now; a call within a wait keeps its time.
func MarkNoteWait(q Q, agent, kind string, now int64) error {
	if _, _, kept, err := NoteWaitKept(q, agent, now); err != nil || kept {
		return err
	}
	return SetMeta(q, noteWaitKey(agent), fmt.Sprintf("%d:%s", now, kind))
}

// ClearNoteWait ends the wait MarkNoteWait kept.
func ClearNoteWait(q Q, agent string) error { return SetMeta(q, noteWaitKey(agent), "") }

// NoteWaitKept is NoteWait while the wait still counts at now (NoteWaitStaleS); ok false for none or a stale one.
func NoteWaitKept(q Q, agent string, now int64) (kind string, since int64, ok bool, err error) {
	kind, since, ok, err = NoteWait(q, agent)
	return kind, since, ok && now-since <= NoteWaitStaleS, err
}

// NoteWait is the kind and start of the wait MarkNoteWait kept, ok false when none is kept.
func NoteWait(q Q, agent string) (kind string, since int64, ok bool, err error) {
	v, _, err := Meta(q, noteWaitKey(agent))
	if err != nil || v == "" {
		return "", 0, false, err
	}
	n, k, found := strings.Cut(v, ":")
	since, perr := strconv.ParseInt(n, 10, 64)
	return k, since, found && perr == nil, nil
}
