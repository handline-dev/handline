package housekeeping

import (
	"fmt"
	"os"
	"path/filepath"
	"syscall"

	"github.com/handline/handline/go/internal/db"
)

const (
	GB = int64(1) << 30
	// DiskLow is the free space under which the founder is told; each further DiskStep down is told again, and the watch is quiet again
	// once free space is back at DiskLow plus one step, so a volume that hovers at the line does not repeat the notice.
	DiskLow       = 20 * GB
	DiskStep      = 5 * GB
	DiskEvery     = int64(600)
	diskToldLevel = "disk_low_told"
)

// DiskWatch tells the founder when the volume the home is on runs low on free space. It reads and tells; it never deletes.
type DiskWatch struct {
	Home string
	Tell func(q db.Q, text string) error
	Log  func(string)
	// Free is the free and total bytes of the volume holding a path; nil is statfs.
	Free func(path string) (free, total int64, err error)

	checked int64
}

func statfsFree(path string) (int64, int64, error) {
	var s syscall.Statfs_t
	if err := syscall.Statfs(path, &s); err != nil {
		return 0, 0, err
	}
	return int64(s.Bavail) * int64(s.Bsize), int64(s.Blocks) * int64(s.Bsize), nil
}

// level is 0 at or above DiskLow, 1 under it, and one more for each further DiskStep under.
func level(free int64) int64 {
	if free >= DiskLow {
		return 0
	}
	return 1 + (DiskLow-free-1)/DiskStep
}

// Check runs at most every DiskEvery seconds. The level told is kept in the database, so a restart does not tell it again.
func (w *DiskWatch) Check(q db.Q, now int64) error {
	if w.checked != 0 && now-w.checked < DiskEvery {
		return nil
	}
	w.checked = now
	free, total, err := w.free()(w.Home)
	if err != nil {
		return err
	}
	told, err := db.Cursor(q, diskToldLevel)
	if err != nil {
		return err
	}
	if told != 0 && free >= DiskLow+DiskStep {
		w.Log(fmt.Sprintf("disk: %d GB free again", free/GB))
		return db.ClearCursor(q, diskToldLevel)
	}
	lv := level(free)
	if lv <= told {
		return nil
	}
	text := fmt.Sprintf("Disk space is low: %d GB free of %d GB on the volume %s is on. The usual growers are the Go build cache "+
		"(%s) and the sessions' scratch (%s). Nothing was deleted.", free/GB, total/GB, w.Home, goBuildCache(), ScratchRoot())
	if next := DiskLow - lv*DiskStep; next > 0 {
		text += fmt.Sprintf(" The next notice is under %d GB.", next/GB)
	}
	if err := w.Tell(q, text); err != nil {
		return err
	}
	w.Log(fmt.Sprintf("disk: %d GB free - founder told", free/GB))
	return db.SetCursor(q, diskToldLevel, lv)
}

func (w *DiskWatch) free() func(string) (int64, int64, error) {
	if w.Free != nil {
		return w.Free
	}
	return statfsFree
}

// goBuildCache is where go keeps its build cache when GOCACHE does not say: named, not measured, since walking it takes minutes.
func goBuildCache() string {
	if c := os.Getenv("GOCACHE"); c != "" {
		return c
	}
	dir, err := os.UserCacheDir()
	if err != nil {
		return "go env GOCACHE"
	}
	return filepath.Join(dir, "go-build")
}
