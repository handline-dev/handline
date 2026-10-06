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
	db, actor := c.srv.DB, c.Caller.Actor()
	mu := requestKey(actor, rid)
	mu.Lock()
	defer mu.Unlock()
	cutoff := time.Now().Add(-ReplyKeep).Unix()
	pkey := paramsKey(params)
	var method, kept, reply string
	err := db.QueryRow("SELECT method, params, reply FROM rpc_replies WHERE caller = ? AND request_id = ? AND ts > ?",
		actor, rid, cutoff).Scan(&method, &kept, &reply)
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
		actor, rid, cutoff).Scan(&method, &result)
	switch {
	case err == nil: // the write landed and the daemon died before its reply was kept: answer, repeat nothing
		if method != name {
			return nil, differentCall(rid)
		}
		return replayed(result)
	case err != sql.ErrNoRows:
		return nil, err
	}
	out, err := m.Handler(c, params)
	if err != nil {
		return nil, err
	}
	now := time.Now().Unix()
	if !m.OwnEvent {
		payload := PyJSON(map[string]any{"method": name, "request_id": rid, "actor": c.Caller.ActorInfo()}, false, false)
		if _, err := db.Exec("INSERT INTO events(ts, agent, kind, payload) VALUES(?, ?, 'rpc_write', ?)", now, actor, payload); err != nil {
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
		actor, rid, name, pkey, string(b), now)
	return out, err
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
