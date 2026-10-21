// Package storetest opens a fresh database for a test.
package storetest

import (
	"database/sql"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/store"
)

// Open is a new database in the test's temp dir, closed when the test ends.
func Open(t testing.TB) *sql.DB {
	t.Helper()
	d, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	return d
}
