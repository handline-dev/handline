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

type Watcher struct {
	DB        *sql.DB
	Emit      Emit
	Detectors []Detector
	Log       func(format string, args ...any)
}

// Scan runs every detector once.
func (w *Watcher) Scan() {
	for _, d := range w.Detectors {
		if err := d.Scan(w.DB, w.Emit); err != nil {
			w.Log("  stream: %s: %v", d.Name(), err)
		}
	}
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
			} else if v != version {
				version = v
				w.Scan()
			}
		}
		select {
		case <-ctx.Done():
		case <-time.After(wait):
		}
	}
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
