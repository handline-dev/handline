package structured

import (
	"database/sql"
	"encoding/json"
	"errors"
	"sync"
	"time"
)

// Op is one send as the ledger keeps it. The ledger is what makes a retry safe across a restart: the daemon records an operation
// before it writes the message, and a repeat gets the recorded state back.
type Op struct {
	ID       string
	UUID     string
	Text     string
	Delivery Delivery
	State    State
}

// Ledger keeps the sends of one agent. Put inserts or updates by ID. Pending is the operations still Queued or Sent, oldest first.
type Ledger interface {
	Get(op string) (*Op, error)
	Put(o Op) error
	Pending() ([]Op, error)
}

// MemLedger is a Ledger in memory, for tests and for a session started without a database.
type MemLedger struct {
	mu  sync.Mutex
	ops []Op
}

func (l *MemLedger) Get(id string) (*Op, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	for i := range l.ops {
		if l.ops[i].ID == id {
			o := l.ops[i]
			return &o, nil
		}
	}
	return nil, nil
}

func (l *MemLedger) Put(o Op) error {
	l.mu.Lock()
	defer l.mu.Unlock()
	for i := range l.ops {
		if l.ops[i].ID == o.ID {
			l.ops[i] = o
			return nil
		}
	}
	l.ops = append(l.ops, o)
	return nil
}

func (l *MemLedger) Pending() ([]Op, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	var out []Op
	for _, o := range l.ops {
		if o.State == Queued || o.State == Sent {
			out = append(out, o)
		}
	}
	return out, nil
}

// DBLedger keeps an agent's sends in the operations table (kind "send"), so it needs no schema step of its own: the id is
// "send:<agent>:<op>", step is the delivery, message the text and result_json the message uuid.
type DBLedger struct {
	DB    *sql.DB
	Agent string
	Now   func() time.Time
}

const sendKind = "send"

func (l *DBLedger) key(op string) string { return sendKind + ":" + l.Agent + ":" + op }

func (l *DBLedger) now() int64 {
	if l.Now != nil {
		return l.Now().Unix()
	}
	return time.Now().Unix()
}

func (l *DBLedger) Get(op string) (*Op, error) {
	return scanOp(l.DB.QueryRow("SELECT id, state, step, message, result_json FROM operations WHERE id = ? AND kind = ?", l.key(op), sendKind), l.prefix())
}

func (l *DBLedger) prefix() string { return sendKind + ":" + l.Agent + ":" }

func scanOp(r interface{ Scan(...any) error }, prefix string) (*Op, error) {
	var id, state, step, msg, res sql.NullString
	if err := r.Scan(&id, &state, &step, &msg, &res); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, nil
		}
		return nil, err
	}
	var r2 struct {
		UUID string `json:"uuid"`
	}
	json.Unmarshal([]byte(res.String), &r2)
	return &Op{ID: id.String[len(prefix):], UUID: r2.UUID, Text: msg.String, Delivery: Delivery(step.String), State: State(state.String)}, nil
}

func (l *DBLedger) Put(o Op) error {
	res, _ := json.Marshal(map[string]string{"uuid": o.UUID})
	now := l.now()
	_, err := l.DB.Exec("INSERT INTO operations(id, kind, state, step, message, result_json, agent, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?) "+
		"ON CONFLICT(id) DO UPDATE SET state = excluded.state, step = excluded.step, message = excluded.message, result_json = excluded.result_json, updated_at = excluded.updated_at",
		l.key(o.ID), sendKind, string(o.State), string(o.Delivery), o.Text, string(res), l.Agent, now, now)
	return err
}

func (l *DBLedger) Pending() ([]Op, error) {
	rows, err := l.DB.Query("SELECT id, state, step, message, result_json FROM operations WHERE kind = ? AND agent = ? AND state IN (?, ?) ORDER BY created_at, rowid",
		sendKind, l.Agent, string(Queued), string(Sent))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Op
	for rows.Next() {
		o, err := scanOp(rows, l.prefix())
		if err != nil {
			return nil, err
		}
		out = append(out, *o)
	}
	return out, rows.Err()
}
