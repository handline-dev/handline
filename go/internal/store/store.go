// Package store opens the daemon's sqlite database and brings its schema up, as handline/db/schema.py does.
package store

import (
	"database/sql"
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	_ "modernc.org/sqlite" // pure Go: the launchd build needs no C toolchain
)

//go:embed migrations
var migrationFiles embed.FS

const (
	migrateWait    = 60 * time.Second
	RestartCommand = "hl service install"
	busyTimeoutMS  = 10000 // db.connect's timeout=10.0
)

// ErrSchema means the database cannot be used by this code as it stands.
var ErrSchema = errors.New("schema")

type migration struct {
	version  int
	name     string
	additive bool
	sql      string
}

// migrations are the steps manifest.json lists, which tools/go_schema.py writes from handline/db/schema.py:
// the Go store knows no step the Python one does not.
func migrations() ([]migration, error) {
	raw, err := migrationFiles.ReadFile("migrations/manifest.json")
	if err != nil {
		return nil, err
	}
	var steps []struct {
		Version  int    `json:"version"`
		Name     string `json:"name"`
		Additive bool   `json:"additive"`
	}
	if err := json.Unmarshal(raw, &steps); err != nil {
		return nil, fmt.Errorf("migrations/manifest.json: %w", err)
	}
	var out []migration
	for i, st := range steps {
		if st.Version != i+1 {
			return nil, fmt.Errorf("migrations/manifest.json: step %d is numbered %d", i+1, st.Version)
		}
		body, err := migrationFiles.ReadFile(fmt.Sprintf("migrations/%04d_%s.sql", st.Version, st.Name))
		if err != nil {
			return nil, err
		}
		out = append(out, migration{st.Version, st.Name, st.Additive, string(body)})
	}
	return out, nil
}

// LatestVersion is the schema version this code knows.
func LatestVersion() int {
	ms, err := migrations()
	if err != nil || len(ms) == 0 {
		return 0
	}
	return ms[len(ms)-1].version
}

// Open opens the database the way db.connect does (WAL, synchronous NORMAL, foreign keys, a 10 s busy
// timeout) and brings it to the latest schema under a lock file, so two processes opening an old
// database at once migrate it once. A newer database is refused.
//
// A database that has had no migration but already holds tables is a pre-versioning one: only the
// Python daemon's baseline knows how to bring those forward, so it is refused with the command that
// runs it, until that baseline is ported.
func Open(path string) (*sql.DB, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, err
	}
	// One connection per pragma set: modernc applies _pragma to every connection the pool opens.
	dsn := "file:" + path + "?_pragma=journal_mode(WAL)&_pragma=synchronous(NORMAL)&_pragma=foreign_keys(1)" +
		"&_pragma=busy_timeout(" + strconv.Itoa(busyTimeoutMS) + ")&_txlock=immediate"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, err
	}
	if err := bringUp(db, path); err != nil {
		db.Close()
		return nil, err
	}
	return db, nil
}

func version(db *sql.DB) (int, error) {
	var v int
	err := db.QueryRow("SELECT COALESCE(MAX(version), 0) FROM schema_version").Scan(&v)
	if err != nil && strings.Contains(err.Error(), "no such table") {
		return 0, nil
	}
	return v, err
}

func tooNew(path string, have, latest int) error {
	return fmt.Errorf("%w: %s is at schema version %d, newer than the %d this code knows: update handline before using it",
		ErrSchema, path, have, latest)
}

func hasAdditiveColumn(q interface {
	Query(string, ...any) (*sql.Rows, error)
}) (bool, error) {
	rows, err := q.Query("PRAGMA table_info(schema_version)")
	if err != nil {
		return false, err
	}
	defer rows.Close()
	for rows.Next() {
		var cid, notnull, pk int
		var name, typ string
		var dflt sql.NullString
		if err := rows.Scan(&cid, &name, &typ, &notnull, &dflt, &pk); err != nil {
			return false, err
		}
		if name == "additive" {
			return true, nil
		}
	}
	return false, rows.Err()
}

// newerStepsOnlyAdd is true when every step this code does not know was recorded as additive: it only added
// tables and columns, so this code, which names none of them, runs on the database as it is. A step recorded
// without the mark, or a database with no marks at all, is not trusted.
func newerStepsOnlyAdd(db *sql.DB, latest int) (bool, error) {
	if ok, err := hasAdditiveColumn(db); err != nil || !ok {
		return false, err
	}
	var n int
	err := db.QueryRow("SELECT COUNT(*) FROM schema_version WHERE version > ? AND additive = 0", latest).Scan(&n)
	return n == 0, err
}

// byHand are the steps tools/go_schema.py cannot dump because they change a table an earlier step made: ported here,
// run after the step's generated SQL (a comment only).
var byHand = map[int]func(*sql.Tx) error{
	10: addExtraWorktrees, // agent_extra_worktrees
}

// addExtraWorktrees is _agent_extra_worktrees in schema.py: a database made before the column has none.
func addExtraWorktrees(tx *sql.Tx) error {
	var n int
	if err := tx.QueryRow("SELECT COUNT(*) FROM pragma_table_info('agent_state') WHERE name = 'extra_worktrees'").Scan(&n); err != nil || n > 0 {
		return err
	}
	_, err := tx.Exec("ALTER TABLE agent_state ADD COLUMN extra_worktrees TEXT")
	return err
}

func bringUp(db *sql.DB, path string) error {
	ms, err := migrations()
	if err != nil {
		return err
	}
	latest := ms[len(ms)-1].version
	if have, err := version(db); err != nil {
		return err
	} else if have == latest {
		return nil
	} else if have > latest {
		if ok, err := newerStepsOnlyAdd(db, latest); err != nil {
			return err
		} else if !ok {
			return tooNew(path, have, latest)
		}
		return nil
	}
	lock, err := takeLock(path + ".migrate-lock")
	if err != nil {
		return err
	}
	defer lock.Close() // closing the file drops the flock
	have, err := version(db)
	if err != nil {
		return err
	}
	if have > latest {
		if ok, err := newerStepsOnlyAdd(db, latest); err != nil {
			return err
		} else if !ok {
			return tooNew(path, have, latest)
		}
		return nil // another process brought it ahead while this one waited for the lock
	}
	if have == 0 {
		var n int
		if err := db.QueryRow("SELECT COUNT(*) FROM sqlite_master").Scan(&n); err != nil {
			return err
		}
		if n > 0 {
			return fmt.Errorf("%w: %s predates schema versions: run `%s` to restart the Python daemon, which brings it up",
				ErrSchema, path, RestartCommand)
		}
	}
	for _, m := range ms {
		if m.version <= have {
			continue
		}
		tx, err := db.Begin()
		if err != nil {
			return err
		}
		if _, err := tx.Exec(m.sql); err != nil {
			tx.Rollback()
			return fmt.Errorf("migration %d (%s): %w", m.version, m.name, err)
		}
		if step := byHand[m.version]; step != nil {
			if err := step(tx); err != nil {
				tx.Rollback()
				return fmt.Errorf("migration %d (%s): %w", m.version, m.name, err)
			}
		}
		if _, err := tx.Exec("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, " +
			"name TEXT NOT NULL, applied_at INTEGER NOT NULL, additive INTEGER NOT NULL DEFAULT 0)"); err != nil {
			tx.Rollback()
			return err
		}
		if err := markAdditiveColumn(tx, ms); err != nil {
			tx.Rollback()
			return err
		}
		if _, err := tx.Exec("INSERT INTO schema_version(version, name, applied_at, additive) VALUES(?, ?, ?, ?)",
			m.version, m.name, time.Now().Unix(), m.additive); err != nil {
			tx.Rollback()
			return err
		}
		if err := tx.Commit(); err != nil {
			return err
		}
	}
	return nil
}

// markAdditiveColumn gives a database stamped before steps said whether they only add the column, filled
// from the steps this code knows (_mark_additive_column in schema.py).
func markAdditiveColumn(tx *sql.Tx, ms []migration) error {
	if ok, err := hasAdditiveColumn(tx); err != nil || ok {
		return err
	}
	if _, err := tx.Exec("ALTER TABLE schema_version ADD COLUMN additive INTEGER NOT NULL DEFAULT 0"); err != nil {
		return err
	}
	for _, m := range ms {
		if _, err := tx.Exec("UPDATE schema_version SET additive = ? WHERE version = ?", m.additive, m.version); err != nil {
			return err
		}
	}
	return nil
}

// takeLock is the lock the Python migrate() takes on the same file, so the two daemons exclude each other.
func takeLock(file string) (*os.File, error) {
	f, err := os.OpenFile(file, os.O_RDWR|os.O_CREATE, 0o644)
	if err != nil {
		return nil, err
	}
	deadline := time.Now().Add(migrateWait)
	for {
		if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err == nil {
			break
		} else if err != syscall.EWOULDBLOCK {
			f.Close()
			return nil, err
		}
		if time.Now().After(deadline) {
			holder := "another process"
			if b, _ := os.ReadFile(file); len(strings.TrimSpace(string(b))) > 0 {
				holder = strings.TrimSpace(string(b))
			}
			f.Close()
			return nil, fmt.Errorf("%w: %s has been busy migrating for %.0fs, held by pid %s: wait for it, or end it and try again",
				ErrSchema, file, migrateWait.Seconds(), holder)
		}
		time.Sleep(50 * time.Millisecond)
	}
	f.Truncate(0)
	f.WriteString(strconv.Itoa(os.Getpid()))
	return f, nil
}

// OpenShared opens a database another daemon owns and migrates: it creates nothing and changes no schema, and
// refuses a database that is not already at this code's schema or an additive step ahead of it. A gateway uses it,
// so the Python daemon is the only one that brings the database forward.
func OpenShared(path string) (*sql.DB, error) {
	if _, err := os.Stat(path); err != nil {
		return nil, fmt.Errorf("%w: %s does not exist: the daemon that owns it brings it up first", ErrSchema, path)
	}
	dsn := "file:" + path + "?_pragma=foreign_keys(1)&_pragma=busy_timeout(" + strconv.Itoa(busyTimeoutMS) + ")&_txlock=immediate"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, err
	}
	have, err := version(db)
	if err == nil {
		if latest := LatestVersion(); have < latest {
			err = fmt.Errorf("%w: %s is at schema version %d, older than the %d this code needs: start the daemon that owns it first",
				ErrSchema, path, have, latest)
		} else if have > latest {
			var ok bool
			if ok, err = newerStepsOnlyAdd(db, latest); err == nil && !ok {
				err = tooNew(path, have, latest)
			}
		}
	}
	if err != nil {
		db.Close()
		return nil, err
	}
	return db, nil
}
