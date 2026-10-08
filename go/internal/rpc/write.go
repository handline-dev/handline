package rpc

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"hash/fnv"
	"sync"
	"time"
)

const (
	// ReplyKeep is how long a write's reply and effects are kept for a retry.
	ReplyKeep    = 24 * time.Hour
	requestIDMax = 128
)

// retryLocks are striped by request id: a few unrelated writes may wait for each other, none runs twice.
var retryLocks [64]sync.Mutex

func requestKey(actor, rid string) *sync.Mutex {
	h := fnv.New32a()
	h.Write([]byte(actor + "\x00" + rid))
	return &retryLocks[h.Sum32()%uint32(len(retryLocks))]
}

func paramsKey(params map[string]any) string {
	rest := make(map[string]any, len(params))
	for k, v := range params {
		if k != "request_id" {
			rest[k] = v
		}
	}
	return PyJSON(rest, true, false)
}

func differentCall(rid string) *Error {
	return BadRequestError(fmt.Sprintf("request_id %q was used for a different call", rid), "request_id", BadRequest)
}

// runWrite runs a write once per request id, as handline/daemon/rpc.py _write does over the same tables. The reply
// of a success is kept a day; a retry with the same id and params gets it back marked replayed and repeats nothing.
// Refusals and errors are not kept, so a retry is judged again.
func (c *Conn) runWrite(name string, m Method, params map[string]any) (any, error) {
	rid, ok := params["request_id"].(string)
	if !ok || rid == "" || len([]rune(rid)) > requestIDMax {
		return nil, BadRequestError(fmt.Sprintf("%s is a write: it takes a request_id, a string of 1 to %d characters", name, requestIDMax),
			"request_id", BadRequest)
	}
	db, actor, writer := c.srv.DB, c.Caller.Actor(), c.Caller.Writer()
	if m.Unkept {
		out, err := m.Handler(c, params)
		if err == nil && !m.OwnEvent && !c.srv.NoEvents {
			err = writeEvent(db, actor, c.Caller, name, rid, time.Now().Unix())
		}
		return out, err
	}
	mu := requestKey(writer, rid)
	mu.Lock()
	defer mu.Unlock()
	cutoff := time.Now().Add(-ReplyKeep).Unix()
	pkey := paramsKey(params)
	var method, kept, reply string
	err := db.QueryRow("SELECT method, params, reply FROM rpc_replies WHERE caller = ? AND request_id = ? AND ts > ?",
		writer, rid, cutoff).Scan(&method, &kept, &reply)
	switch {
	case err == nil:
		if method != name || kept != pkey {
			return nil, differentCall(rid)
		}
		return replayed(reply)
	case err != sql.ErrNoRows:
		return nil, err
	}
	var result string
	err = db.QueryRow("SELECT method, result_json FROM write_effects WHERE caller = ? AND request_id = ? AND created_at > ?",
		writer, rid, cutoff).Scan(&method, &result)
	switch {
	case err == nil: // the write landed and the daemon died before its reply was kept: answer, repeat nothing
		if method != name {
			return nil, differentCall(rid)
		}
		return replayed(result)
	case err != sql.ErrNoRows:
		return nil, err
	}
	c.callName, c.callRID = name, rid
	out, err := m.Handler(c, params)
	c.callName, c.callRID = "", ""
	if err != nil {
		return nil, err
	}
	now := time.Now().Unix()
	if !m.OwnEvent {
		if err := writeEvent(db, actor, c.Caller, name, rid, now); err != nil {
			return nil, err
		}
	}
	if _, err := db.Exec("DELETE FROM rpc_replies WHERE ts <= ?", now-int64(ReplyKeep.Seconds())); err != nil {
		return nil, err
	}
	if _, err := db.Exec("DELETE FROM write_effects WHERE created_at <= ?", now-int64(ReplyKeep.Seconds())); err != nil {
		return nil, err
	}
	b, err := json.Marshal(out)
	if err != nil {
		return nil, err
	}
	_, err = db.Exec("INSERT OR REPLACE INTO rpc_replies(caller, request_id, method, params, reply, ts) VALUES(?,?,?,?,?,?)",
		writer, rid, name, pkey, string(b), now)
	return out, err
}

// CommitThen is what an Effect function returns to commit its writes and still answer with Err.
type CommitThen struct{ Err error }

func (c *CommitThen) Error() string { return c.Err.Error() }

// Effect runs the effect of the write in hand in one transaction with the record of its result (write_effects), as
// handline/daemon/rpc_taskverbs.py does: a retry after a crash between the two finds the record and answers, and repeats
// nothing. fn gets the transaction and returns the result the caller is told; an error rolls everything back.
func (c *Conn) Effect(fn func(tx *sql.Tx) (map[string]any, error)) (map[string]any, error) {
	if c.callRID == "" {
		return nil, fmt.Errorf("rpc: Effect is for a method registered with Write")
	}
	tx, err := c.srv.DB.Begin()
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	out, err := fn(tx)
	if keep, ok := err.(*CommitThen); ok {
		// a refusal whose writes stand (a denied request, a throttle): no record is kept, so a retry is judged again
		if err := tx.Commit(); err != nil {
			return nil, err
		}
		return nil, keep.Err
	}
	if err != nil {
		return nil, err
	}
	b, err := json.Marshal(out)
	if err != nil {
		return nil, err
	}
	if _, err := tx.Exec("INSERT OR REPLACE INTO write_effects(caller, request_id, method, result_json, created_at) VALUES(?,?,?,?,?)",
		c.Caller.Writer(), c.callRID, c.callName, string(b), time.Now().Unix()); err != nil {
		return nil, err
	}
	return out, tx.Commit()
}

// replayed is a kept reply with replayed set.
func replayed(kept string) (any, error) {
	m := map[string]any{}
	if err := json.Unmarshal([]byte(kept), &m); err != nil {
		return nil, err
	}
	m["replayed"] = true
	return m, nil
}

// writeEvent is the generic event a write leaves when it writes none of its own.
func writeEvent(db *sql.DB, actor string, caller Caller, name, rid string, now int64) error {
	payload := PyJSON(map[string]any{"method": name, "request_id": rid, "actor": caller.ActorInfo()}, false, false)
	_, err := db.Exec("INSERT INTO events(ts, agent, kind, payload) VALUES(?, ?, 'rpc_write', ?)", now, actor, payload)
	return err
}
