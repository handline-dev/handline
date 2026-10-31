// Package watcher turns change in the database into rows of the event stream, for the topics whose detector has moved here from the Python
// daemon's watcher. It looks every Every for a commit by another connection, and then runs each detector, which compares what it
// last saw with what the database shows now and appends an event for each difference. The first look of a detector says nothing: a
// client's snapshot already shows what is there.
//
// While the Python watcher still runs it writes these same rows, and a second writer would double every event, so the daemon starts this
// only once the Python one has stopped (-cutover). Retention is the stream hub's hourly prune.
package watcher

import (
	"context"
	"database/sql"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/stream"
)

const (
	// Every is how often the database is looked at for change.
	Every = 250 * time.Millisecond
	// Retry is the wait after the watch failed, before it tries again.
	Retry = 2 * time.Second
)

// Emit appends one event to the stream (stream.Hub.Append).
type Emit func(stream.Entry) (*stream.Event, error)

// Detector is one source of change. Scan is called with the database and Emit when something committed, and keeps its own state between
// calls; an error is logged and does not stop the other detectors.
type Detector interface {
	Name() string
	Scan(q *sql.DB, emit Emit) error
}

// Tabled is a detector whose input is the tables it names (with the Watcher's Signature, for what is not in the database): it is run when
// the count of one of them has moved (table_changes, kept by triggers whoever writes) and not on every commit. A detector that is not
// Tabled is run on every change, as before. tables_test.go fails a detector whose reads touch a table it does not name.
type Tabled interface {
	Tables() []string
}

// Pending is a detector with work the database does not announce (a window that may have ended): it is run on every tick while it says so.
type Pending interface {
	Pending() bool
}

type Watcher struct {
	DB        *sql.DB
	Emit      Emit
	Detectors []Detector
	Log       func(format string, args ...any)
	// Signature says whether something the database does not hold has changed (the hr proposals are files): a change in it wakes the loop
	// as a commit does. Nil is none.
	Signature func() string
	counted   map[Detector]map[string]int64 // the counts each Tabled detector last ran at
}

// Scan runs every detector once.
func (w *Watcher) Scan() {
	for _, d := range w.Detectors {
		if err := d.Scan(w.DB, w.Emit); err != nil {
			w.Log("  stream: %s: %v", d.Name(), err)
		}
	}
}

// scanMoved runs the detectors a change reaches: all of them when all is set (the first look, or the Signature moved), else the ones that
// are not Tabled, the Pending ones and those whose tables have a new count. The counts are read before the runs, so a commit during a run
// moves them again and the detector runs again at the next look.
func (w *Watcher) scanMoved(ctx context.Context, conn *sql.Conn, all bool) {
	counts, err := tableCounts(ctx, conn)
	if err != nil {
		w.fail(err) // no counts (a database without the migration): every detector runs
		all = true
	}
	if w.counted == nil {
		w.counted = map[Detector]map[string]int64{}
	}
	for _, d := range w.Detectors {
		at := map[string]int64{}
		run := all
		if t, ok := d.(Tabled); !ok {
			run = true
		} else {
			for _, name := range t.Tables() {
				n, has := counts[name]
				at[name] = n
				if prev, seen := w.counted[d][name]; !seen || !has || prev != n {
					run = true
				}
			}
			if w.counted[d] == nil {
				run = true
			}
		}
		if p, ok := d.(Pending); ok && p.Pending() {
			run = true
		}
		if !run {
			continue
		}
		w.counted[d] = at
		if err := d.Scan(w.DB, w.Emit); err != nil {
			w.Log("  stream: %s: %v", d.Name(), err)
		}
	}
}

// tableCounts is table_changes: how many changes each table has had.
func tableCounts(ctx context.Context, conn *sql.Conn) (map[string]int64, error) {
	rows, err := conn.QueryContext(ctx, "SELECT name, n FROM table_changes")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]int64{}
	for rows.Next() {
		var name string
		var n int64
		if err := rows.Scan(&name, &n); err != nil {
			return nil, err
		}
		out[name] = n
	}
	return out, rows.Err()
}

func (w *Watcher) pending() bool {
	for _, d := range w.Detectors {
		if p, ok := d.(Pending); ok && p.Pending() {
			return true
		}
	}
	return false
}

// Run scans at once (each detector's first look) and then whenever another connection has committed, until stop closes. The watch holds
// one connection of its own, since data_version is per connection; one that fails is dropped and taken again after Retry.
func (w *Watcher) Run(stop <-chan struct{}) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() { <-stop; cancel() }()
	var conn *sql.Conn
	defer func() {
		if conn != nil {
			conn.Close()
		}
	}()
	var version int64 = -1
	sig := "\x00" // never a signature: the first look is a change
	for ctx.Err() == nil {
		wait := Every
		if conn == nil {
			var err error
			if conn, err = w.DB.Conn(ctx); err != nil {
				w.fail(err)
				wait = Retry
			}
			version = -1 // a new connection's first look is a change
		}
		if conn != nil {
			var v int64
			if err := conn.QueryRowContext(ctx, "PRAGMA data_version").Scan(&v); err != nil {
				w.fail(err)
				conn.Close()
				conn, wait = nil, Retry
			} else if now := w.signature(); v != version || now != sig || w.pending() {
				all := now != sig || version == -1
				version, sig = v, now
				w.scanMoved(ctx, conn, all)
			}
		}
		select {
		case <-ctx.Done():
		case <-time.After(wait):
		}
	}
}

func (w *Watcher) signature() string {
	if w.Signature == nil {
		return ""
	}
	return w.Signature()
}

func (w *Watcher) fail(err error) {
	if err != context.Canceled {
		w.Log("  stream: watcher: %v", err)
	}
}

// Prime is where a detector last looked, a cursor in the database, so what happened while the daemon was down is said when it is back.
// A cursor never set starts at the newest row (newestSQL): what happened before the stream existed is not news.
func Prime(q db.Q, name, newestSQL string) (int64, error) {
	if _, set, err := db.CursorUpdated(q, name); err != nil {
		return 0, err
	} else if !set {
		var n int64
		if err := q.QueryRow(newestSQL).Scan(&n); err != nil {
			return 0, err
		}
		if err := db.SetCursor(q, name, n); err != nil {
			return 0, err
		}
	}
	return db.Cursor(q, name)
}
