// Package store opens the daemon's sqlite database and brings its schema up, as handline/db/schema.py does.
package store

import (
	"database/sql"
	"embed"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"

	_ "modernc.org/sqlite" // pure Go: the launchd build needs no C toolchain
)

//go:embed migrations/*.sql
var migrationFiles embed.FS

const (
	migrateWait    = 60 * time.Second
	RestartCommand = "hl service install"
	busyTimeoutMS  = 10000 // db.connect's timeout=10.0
)

// ErrSchema means the database cannot be used by this code as it stands.
var ErrSchema = errors.New("schema")

type migration struct {
	version int
	name    string
	sql     string
}

func migrations() ([]migration, error) {
	entries, err := migrationFiles.ReadDir("migrations")
	if err != nil {
		return nil, err
	}
	var out []migration
	for _, e := range entries {
		base := strings.TrimSuffix(e.Name(), ".sql")
		num, name, ok := strings.Cut(base, "_")
		v, err := strconv.Atoi(num)
		if !ok || err != nil {
			return nil, fmt.Errorf("migration file %q is not NNNN_name.sql", e.Name())
		}
		body, err := migrationFiles.ReadFile("migrations/" + e.Name())
		if err != nil {
			return nil, err
		}
		out = append(out, migration{v, name, string(body)})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].version < out[j].version })
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
		return tooNew(path, have, latest)
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
		return tooNew(path, have, latest)
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
		if _, err := tx.Exec("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, " +
			"name TEXT NOT NULL, applied_at INTEGER NOT NULL)"); err != nil {
			tx.Rollback()
			return err
		}
		if _, err := tx.Exec("INSERT INTO schema_version(version, name, applied_at) VALUES(?, ?, ?)",
			m.version, m.name, time.Now().Unix()); err != nil {
			tx.Rollback()
			return err
		}
		if err := tx.Commit(); err != nil {
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
