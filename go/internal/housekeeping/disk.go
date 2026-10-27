package housekeeping

import (
	"fmt"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/sysres"
)

const (
	GB = int64(1) << 30
	// DiskLow is the free space under which the founder is told once (a notifications disk_low event, which a client shows as a toast beside
	// its status-bar warning; never a task). It is not told again until free space is back at DiskLow plus DiskMargin, so a volume that
	// hovers at the line does not tell again and again.
	DiskLow       = 10 * GB
	DiskMargin    = 2 * GB
	DiskEvery     = int64(600)
	diskToldLevel = "disk_low_told"
	// DiskLowEvent is the events row the notifications topic turns into a disk_low notification.
	DiskLowEvent = "disk_low"
)

// DiskWatch tells the founder when the volume the home is on runs low on free space. It reads and tells; it never deletes.
type DiskWatch struct {
	Home string
	Log  func(string)
	// Free is the free and total bytes of the volume holding a path; nil is statfs.
	Free func(path string) (free, total int64, err error)

	checked int64
}

// Check runs at most every DiskEvery seconds. That the founder was told is kept in the database, so a restart does not tell again.
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
	if told != 0 && free >= DiskLow+DiskMargin {
		w.Log(fmt.Sprintf("disk: %d GB free again", free/GB))
		return db.ClearCursor(q, diskToldLevel)
	}
	if told != 0 || free >= DiskLow {
		return nil
	}
	if _, err := db.AddEvent(q, "", DiskLowEvent, db.KV{K: "free_bytes", V: free}, db.KV{K: "total_bytes", V: total}); err != nil {
		return err
	}
	w.Log(fmt.Sprintf("disk: %d GB free - told once", free/GB))
	return db.SetCursor(q, diskToldLevel, 1)
}

func (w *DiskWatch) free() func(string) (int64, int64, error) {
	if w.Free != nil {
		return w.Free
	}
	return sysres.StatfsFree
}
