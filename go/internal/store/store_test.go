package store

import (
	"database/sql"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

func TestFreshDatabaseIsSetUpWhole(t *testing.T) {
	db, err := Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if v, _ := version(db); v != LatestVersion() {
		t.Fatalf("version %d, want %d", v, LatestVersion())
	}
	var mode string
	db.QueryRow("PRAGMA journal_mode").Scan(&mode)
	if mode != "wal" {
		t.Fatalf("journal_mode %q", mode)
	}
	var busy, fk int
	db.QueryRow("PRAGMA busy_timeout").Scan(&busy)
	db.QueryRow("PRAGMA foreign_keys").Scan(&fk)
	if busy != busyTimeoutMS || fk != 1 {
		t.Fatalf("busy_timeout %d foreign_keys %d", busy, fk)
	}
}

func TestReopenChangesNothing(t *testing.T) {
	p := filepath.Join(t.TempDir(), "db.sqlite")
	for i := 0; i < 2; i++ {
		db, err := Open(p)
		if err != nil {
			t.Fatal(err)
		}
		var n int
		db.QueryRow("SELECT COUNT(*) FROM schema_version").Scan(&n)
		if n != LatestVersion() {
			t.Fatalf("open %d: %d version rows", i, n)
		}
		db.Close()
	}
}

func TestNewerDatabaseIsRefused(t *testing.T) {
	p := filepath.Join(t.TempDir(), "db.sqlite")
	db, _ := Open(p)
	db.Exec("INSERT INTO schema_version VALUES(999, 'future', 0)")
	db.Close()
	if _, err := Open(p); !errors.Is(err, ErrSchema) {
		t.Fatalf("got %v", err)
	}
}

func TestPreVersionDatabaseIsLeftToThePythonDaemon(t *testing.T) {
	p := filepath.Join(t.TempDir(), "db.sqlite")
	db, _ := Open(p)
	db.Exec("DROP TABLE schema_version")
	db.Close()
	_, err := Open(p)
	if !errors.Is(err, ErrSchema) {
		t.Fatalf("got %v", err)
	}
}

func TestOldDatabaseMigratesOnceUnderConcurrentOpens(t *testing.T) {
	p := filepath.Join(t.TempDir(), "db.sqlite")
	db, _ := Open(p)
	db.Exec("DROP TABLE rpc_replies")
	db.Exec("DELETE FROM schema_version WHERE version = 2")
	db.Close()
	var wg sync.WaitGroup
	errs := make(chan error, 8)
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			d, err := Open(p)
			if err == nil {
				d.Close()
			}
			errs <- err
		}()
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	db, _ = Open(p)
	defer db.Close()
	var n int
	db.QueryRow("SELECT COUNT(*) FROM schema_version WHERE version = 2").Scan(&n)
	if n != 1 {
		t.Fatalf("migration 2 recorded %d times", n)
	}
}

// The Go baseline and the Python one must make the same database: a fresh Go one is compared with a
// fresh one from handline/db/schema.py, table by table.
func TestFreshSchemaMatchesPython(t *testing.T) {
	py, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("no python3")
	}
	root, _ := filepath.Abs("../../..")
	if _, err := os.Stat(filepath.Join(root, "handline", "db", "schema.py")); err != nil {
		t.Skip("not inside the handline repo")
	}
	pyDB := filepath.Join(t.TempDir(), "py.sqlite")
	cmd := exec.Command(py, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; "+
		"from handline.db import schema; schema.connect(Path(sys.argv[2])).close()", root, pyDB)
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Skipf("python could not make a database: %v\n%s", err, out)
	}
	goDB, err := Open(filepath.Join(t.TempDir(), "go.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer goDB.Close()
	pyCon, err := Open(pyDB) // already current: Open only reads it
	if err != nil {
		t.Fatal(err)
	}
	defer pyCon.Close()
	a, b := masterOf(t, goDB), masterOf(t, pyCon)
	for k, v := range a {
		if b[k] != v {
			t.Errorf("%s differs:\n go: %s\n py: %s", k, v, b[k])
		}
	}
	for k := range b {
		if _, ok := a[k]; !ok {
			t.Errorf("%s only in Python's database", k)
		}
	}
}

// masterOf maps each table, index and trigger to its normalised sql; sqlite's own tables are left out.
func masterOf(t *testing.T, db *sql.DB) map[string]string {
	t.Helper()
	rows, err := db.Query("SELECT type, name, COALESCE(sql, '') FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	out := map[string]string{}
	for rows.Next() {
		var typ, name, def string
		rows.Scan(&typ, &name, &def)
		out[typ+" "+name] = strings.Join(strings.Fields(withoutComments(def)), " ")
	}
	return out
}

func withoutComments(sql string) string {
	var out []string
	for _, line := range strings.Split(sql, "\n") {
		if i := strings.Index(line, "--"); i >= 0 && !strings.ContainsAny(line[:i], "'\"") {
			line = line[:i]
		}
		out = append(out, line)
	}
	return strings.Join(out, "\n")
}
